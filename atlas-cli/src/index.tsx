#!/usr/bin/env bun
/**
 * Atlas Code CLI — agentic terminal coding assistant
 *
 * Usage:
 *   atlas                         Interactive chat + agentic mode
 *   atlas --model atlas-plan      Override model for this session
 *   atlas --system "..."          Custom system prompt
 *   atlas --url http://...        Base URL override
 *   atlas --no-agent              Plain chat only (no tools)
 *   atlas --auto-model            Auto-route model by task type
 *   atlas config                  Show config
 *   atlas config set <k> <v>      Persist a config value
 *   atlas init                    Initialise .atlas/ workspace
 *   atlas doctor                  Check configuration health
 *   atlas diff                    Show git diff
 *   atlas git                     Show git status
 *   atlas review                  Review staged changes
 *   atlas commit-message          Generate a commit message
 */

import React, { useState, useCallback, useRef, useEffect } from "react";
import { render, Box, Text, Static, useApp, useInput } from "ink";
import { Mascot } from "./components/Mascot.js";
import Spinner from "ink-spinner";
import meow from "meow";

import { loadConfig, saveConfig, getConfigPath, AtlasConfig } from "./config.js";
import { SetupScreen } from "./components/SetupScreen.js";
import { Message, streamCompletion, completeSync, UsageInfo, stripToolBlocks } from "./api.js";
import { MessageList } from "./components/MessageList.js";
import { InputBox } from "./components/InputBox.js";
import { StatusBar } from "./components/StatusBar.js";
import { ApprovalPrompt } from "./components/ApprovalPrompt.js";
import { ToolCallDisplay } from "./components/ToolCallDisplay.js";
import { DiffViewer } from "./components/DiffViewer.js";
import { Banner } from "./components/Banner.js";
import { MarkdownRenderer } from "./components/MarkdownRenderer.js";

import { detectWorkspace, WorkspaceInfo } from "./workspace.js";
import { checkForUpdates } from "./updater.js";
import { loadProjectContext, loadCustomCommands, saveMemory } from "./project-context.js";
import { saveSession, listSessions, loadSession, newSessionId, deleteOldSessions } from "./session.js";
import { loadPermissions, savePermissions, PermissionConfig } from "./permissions.js";
import { AuditLog } from "./audit.js";
import { runAgentLoop, buildSystemPrompt, AgentEvent } from "./agent-loop.js";
import { handleSlashCommand, parseSlashCommand } from "./slash-commands.js";
import { runAtlasInit } from "./atlas-init.js";
import { toolHandlers } from "./tools/implementations.js";
import { MODEL_DESCRIPTIONS } from "./model-router.js";
import { ToolCall } from "./tools/types.js";

// ---------------------------------------------------------------------------
// CLI args
// ---------------------------------------------------------------------------

const cli = meow(
  `
  Usage
    $ atlas [options] [command]

  Commands
    (none)             Interactive agentic chat
    ask <prompt>       One-shot: get answer printed to stdout (no TUI)
    config             Show config
    config set <k> <v> Set a config value
    init               Initialise .atlas/ workspace
    doctor             Check configuration health
    diff               Show git diff
    git                Show git status
    review             Review staged changes
    commit-message     Generate a commit message
    update             Update Atlas CLI to the latest version

  Pipe mode
    cat file | atlas          Analyse file content
    cat error.log | atlas "fix this"   Combine piped content with a prompt

  Options
    --model,     -m    Atlas model (default: from config or atlas-code)
    --system,    -s    System prompt override
    --url,       -u    Base URL override
    --key,       -k    API key override
    --no-agent         Plain chat, no tool use
    --auto-model       Auto-route model by task type

  Examples
    $ atlas
    $ atlas init
    $ atlas --model atlas-plan
    $ atlas --auto-model
    $ atlas config set apiKey sk-atlas-xxx
    $ atlas config set baseUrl https://atlas.alpheric.ai/v1
`,
  {
    importMeta: import.meta,
    flags: {
      model:     { type: "string",  shortFlag: "m" },
      system:    { type: "string",  shortFlag: "s" },
      url:       { type: "string",  shortFlag: "u" },
      key:       { type: "string",  shortFlag: "k" },
      agent:     { type: "boolean", default: true  },
      autoModel: { type: "boolean", default: false },
    },
  }
);

// ---------------------------------------------------------------------------
// Non-TUI commands (run before ink render)
// ---------------------------------------------------------------------------

async function handleCLICommand(): Promise<boolean> {
  const [cmd, ...rest] = cli.input;

  // ── config ──────────────────────────────────────────────────────────────
  if (cmd === "config") {
    if (rest[0] === "set" && rest[1] && rest[2] !== undefined) {
      const [, key, value] = rest;
      const allowed: (keyof AtlasConfig)[] = ["apiKey", "baseUrl", "model", "stream"];
      if (!allowed.includes(key as keyof AtlasConfig)) {
        console.error(`Unknown config key: ${key}. Valid keys: ${allowed.join(", ")}`);
        process.exit(1);
      }
      const val = key === "stream" ? value === "true" : value;
      saveConfig({ [key]: val } as Partial<AtlasConfig>);
      console.log(`✓ Saved ${key} = ${val}`);
      console.log(`Config: ${getConfigPath()}`);
    } else {
      const cfg = loadConfig();
      console.log(`Config file: ${getConfigPath()}\n`);
      console.log(`  apiKey  : ${cfg.apiKey ? cfg.apiKey.slice(0, 8) + "…" : "(not set)"}`);
      console.log(`  baseUrl : ${cfg.baseUrl}`);
      console.log(`  model   : ${cfg.model}`);
      console.log(`  stream  : ${cfg.stream}`);
    }
    return true;
  }

  // ── init ────────────────────────────────────────────────────────────────
  if (cmd === "init") {
    const workspace = await detectWorkspace();
    const result = await runAtlasInit(workspace.cwd, workspace);
    console.log("✓ Atlas workspace initialised\n");
    if (result.created.length) {
      console.log("Created:");
      result.created.forEach((p) => console.log(`  + ${p}`));
    }
    if (result.skipped.length) {
      console.log("Already existed (skipped):");
      result.skipped.forEach((p) => console.log(`  - ${p}`));
    }
    console.log(`\nEdit ATLAS.md to add project context Atlas will read every session.`);
    return true;
  }

  // ── doctor ──────────────────────────────────────────────────────────────
  if (cmd === "doctor") {
    const cfg = loadConfig();
    const workspace = await detectWorkspace();
    const ok: string[] = [];
    const issues: string[] = [];

    // API key
    if (cfg.apiKey) ok.push("✓  API key configured");
    else issues.push("✗  No API key — run: atlas config set apiKey <key>");

    // Backend reachability
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 5000);
      const res = await fetch(`${cfg.baseUrl.replace(/\/v1.*$/, "")}/health`, {
        signal: controller.signal,
      });
      clearTimeout(timer);
      if (res.ok) ok.push(`✓  Backend reachable (${cfg.baseUrl})`);
      else issues.push(`✗  Backend returned ${res.status} — check baseUrl`);
    } catch {
      issues.push(`✗  Backend unreachable — check: atlas config set baseUrl <url>`);
    }

    // Bun version — check common install locations, not just PATH
    try {
      const { execSync } = await import("child_process");
      const bunPaths = [
        "bun",
        `${process.env.HOME ?? ""}/.bun/bin/bun`,
        "/opt/homebrew/bin/bun",
        "/usr/local/bin/bun",
      ];
      let bunVer = "";
      for (const b of bunPaths) {
        try {
          bunVer = execSync(`"${b}" --version`, { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
          if (bunVer) break;
        } catch { /* try next */ }
      }
      if (bunVer) ok.push(`✓  Bun ${bunVer}`);
      else issues.push("✗  Bun not found — install from https://bun.sh");
    } catch {
      issues.push("✗  Bun not found — install from https://bun.sh");
    }

    // Installed version
    const { localVersion } = await import("./updater.js");
    const ver = localVersion();
    if (ver) ok.push(`✓  Atlas CLI v${ver}`);
    else issues.push("ℹ️  Version unknown (run: atlas update)");

    // Workspace
    if (workspace.isGit) ok.push("✓  Git repository");
    else issues.push("ℹ️  Not a git repository");

    if (workspace.hasAtlasMd) ok.push("✓  ATLAS.md present");
    else issues.push("ℹ️  No ATLAS.md — run: atlas init");

    console.log("\n  Atlas Doctor\n  " + "─".repeat(32));
    [...ok, ...(issues.length ? [""] : []), ...issues].forEach((l) => console.log("  " + l));
    console.log("");
    return true;
  }

  // ── update ──────────────────────────────────────────────────────────────
  if (cmd === "update") {
    const { localVersion, INSTALL_DIR, VERSION_FILE } = await import("./updater.js");
    const fs = await import("fs");
    const path = await import("path");

    const BASE_URL = "https://atlas.alpheric.ai";
    const current = localVersion();

    process.stdout.write("  Checking for updates… ");

    let remote = "";
    try {
      const r = await fetch(`${BASE_URL}/downloads/version.txt`, {
        signal: AbortSignal.timeout(6_000),
      });
      remote = r.ok ? (await r.text()).trim() : "";
    } catch {
      console.log("\n  ✗  Could not reach update server. Check your connection.");
      process.exit(1);
    }

    if (!remote) {
      console.log("\n  ✗  Update server returned no version.");
      process.exit(1);
    }

    if (current === remote) {
      console.log(`\n  ✓  Already up to date (v${current})`);
      process.exit(0);
    }

    console.log(`\n  New version available: v${current || "unknown"} → v${remote}`);
    process.stdout.write("  Downloading… ");

    // Download atlas.js and yoga.wasm directly — no tar extraction needed.
    // This avoids macOS BSD tar --strip-components incompatibilities.
    const distDir = path.join(INSTALL_DIR, "dist");
    try { fs.mkdirSync(distDir, { recursive: true }); } catch {}

    const files = [
      { url: `${BASE_URL}/downloads/atlas.js`,   dest: path.join(distDir, "atlas.js"),   mode: 0o644 },
      { url: `${BASE_URL}/downloads/yoga.wasm`,  dest: path.join(distDir, "yoga.wasm"),  mode: 0o644 },
    ];

    for (const file of files) {
      try {
        const r = await fetch(file.url, { signal: AbortSignal.timeout(120_000) });
        if (!r.ok) throw new Error(`HTTP ${r.status} for ${file.url}`);
        const buf = Buffer.from(await r.arrayBuffer());
        fs.writeFileSync(file.dest, buf, { mode: file.mode });
        // Touch mtime to ensure Bun's bytecode cache is invalidated
        const now = new Date();
        try { fs.utimesSync(file.dest, now, now); } catch {}
      } catch (e: unknown) {
        console.log(`\n  ✗  Download failed: ${e instanceof Error ? e.message : String(e)}`);
        process.exit(1);
      }
    }

    process.stdout.write("done\n  Installing… ");

    fs.writeFileSync(VERSION_FILE, remote + "\n", "utf8");
    console.log(`done\n\n  ✓  Updated to v${remote} — restart atlas to use the new version.\n`);
    process.exit(0);
  }

  // ── ask  — one-shot prompt: atlas ask "explain auth.ts" ─────────────────
  if (cmd === "ask") {
    const prompt = rest.join(" ").trim();
    if (!prompt) { console.error("Usage: atlas ask <prompt>"); process.exit(1); }
    const cfg = loadConfig();
    const config: AtlasConfig = {
      apiKey:  cli.flags.key   ?? cfg.apiKey,
      baseUrl: cli.flags.url   ?? cfg.baseUrl,
      model:   cli.flags.model ?? cfg.model,
      stream:  cfg.stream,
    };
    process.stdout.write(""); // ensure stdout is ready
    try {
      const { streamCompletion } = await import("./api.js");
      const msgs: Message[] = [{ role: "user", content: prompt }];
      for await (const event of streamCompletion(config, msgs)) {
        if (event.type === "text") process.stdout.write(event.content);
      }
      process.stdout.write("\n");
    } catch (e: unknown) {
      console.error("Error:", e instanceof Error ? e.message : String(e));
      process.exit(1);
    }
    return true;
  }

  // ── one-shot git/diff commands ──────────────────────────────────────────
  if (cmd === "diff") {
    const { execSync } = await import("child_process");
    try { console.log(execSync("git diff", { encoding: "utf8" })); } catch (e: unknown) { console.error(e); }
    return true;
  }
  if (cmd === "git") {
    const { execSync } = await import("child_process");
    try { console.log(execSync("git status", { encoding: "utf8" })); } catch (e: unknown) { console.error(e); }
    return true;
  }
  if (cmd === "review") {
    const { execSync } = await import("child_process");
    try { console.log(execSync("git diff --staged", { encoding: "utf8" })); } catch (e: unknown) { console.error(e); }
    return true;
  }
  if (cmd === "commit-message") {
    const workspace = await detectWorkspace();
    const result = await toolHandlers.git_commit_message({}, workspace.cwd);
    console.log(result.output);
    return true;
  }

  return false;
}

// ---------------------------------------------------------------------------
// Chat event log item (what gets rendered)
// ---------------------------------------------------------------------------

type EventItem =
  | { kind: "user"; text: string }
  | { kind: "assistant"; text: string; streaming?: boolean }
  | { kind: "tool"; toolCall: ToolCall; status: "running" | "done" | "denied" | "error"; result?: string }
  | { kind: "diff"; diff: string }
  | { kind: "system"; text: string }
  | { kind: "error"; text: string };

// ---------------------------------------------------------------------------
// Main TUI App
// ---------------------------------------------------------------------------

interface AppProps {
  config: AtlasConfig;
  workspace: WorkspaceInfo;
  permissions: PermissionConfig;
  systemPrompt: string;
  agentEnabled: boolean;
  autoModel: boolean;
  customCommands: Record<string, string>;
  audit: AuditLog;
}

function App({
  config: initialConfig,
  workspace,
  permissions: initialPermissions,
  systemPrompt,
  agentEnabled,
  autoModel,
  customCommands,
  audit,
}: AppProps) {
  const { exit } = useApp();

  const [config, setConfig] = useState(initialConfig);
  const [permissions, setPermissions] = useState(initialPermissions);
  const [messages, setMessages] = useState<Message[]>([]);
  const [events, setEvents] = useState<EventItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [usage, setUsage] = useState<UsageInfo | undefined>();
  const [totalCost, setTotalCost] = useState(0);  // cumulative USD for the session
  const [error, setError] = useState<string | undefined>();
  const [gitBranch, setGitBranch] = useState<string | undefined>();
  // Number of completed user→assistant turns (drives compact header threshold)
  const [turnCount, setTurnCount] = useState(0);

  // ── Mascot state for derived label only (animation lives in <Mascot/>) ─────
  // The actual frame ticking happens INSIDE the Mascot component so it does
  // not re-render the parent App tree on every animation frame (this was the
  // primary flicker source).
  const [mDone, setMDone] = useState(false);
  const prevLoadRef = useRef(false);
  const mState: "idle" | "thinking" | "happy" = loading ? "thinking" : mDone ? "happy" : "idle";

  useEffect(() => {
    if (prevLoadRef.current && !loading) {
      setMDone(true);
      const t = setTimeout(() => setMDone(false), 750);
      return () => clearTimeout(t);
    }
    prevLoadRef.current = loading;
  }, [loading]);

  // Detect git branch once on mount
  useEffect(() => {
    import("child_process").then(({ execSync }) => {
      try {
        const branch = execSync("git branch --show-current", {
          encoding: "utf8",
          stdio: ["ignore", "pipe", "ignore"],
        }).trim();
        if (branch) setGitBranch(branch);
      } catch { /* not a git repo or git not installed */ }
    });
  }, []);

  // ── F10: Auto-save session after every turn ───────────────────────────────
  useEffect(() => {
    if (turnCount === 0 || messages.length === 0) return;
    const firstUser = messages.find(m => m.role === "user");
    const preview = String(firstUser?.content ?? "").slice(0, 100);
    saveSession({
      id: sessionIdRef.current,
      timestamp: new Date().toISOString(),
      workspace: workspace.cwd,
      model: config.model,
      messages,
      preview,
      turnCount,
    });
    // Prune sessions older than 30 days (once per session startup is enough;
    // here we do it on first save only)
    if (turnCount === 1) deleteOldSessions(30);
  }, [turnCount]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Auto-save memory every 3 turns ───────────────────────────────────────
  // Silently writes a fresh summary to .atlas/memory.md in the background so
  // future sessions start with up-to-date context. Never blocks the UI.
  useEffect(() => {
    if (turnCount === 0 || turnCount % 3 !== 0) return;
    if (!workspace.atlasConfigDir || messages.length < 2) return;

    const MEMORY_PROMPT =
      "Summarise our conversation so far into a concise project memory update. " +
      "Include: key decisions made, files changed, current task status, and any " +
      "important context a future session should know. " +
      "Format as clean markdown. Keep it under 400 words. No preamble.";

    const memMessages: Message[] = [
      ...messages,
      { role: "user", content: MEMORY_PROMPT },
    ];

    const atlasDir = workspace.atlasConfigDir;
    const ts = new Date().toISOString().replace("T", " ").slice(0, 16);

    completeSync(config, memMessages)
      .then(({ text }) => {
        if (text.trim()) {
          saveMemory(atlasDir, `# Atlas Memory\n_Last updated: ${ts}_\n\n${text.trim()}\n`);
        }
      })
      .catch(() => { /* silent — memory save should never surface errors */ });
  }, [turnCount]); // eslint-disable-line react-hooks/exhaustive-deps

  // Approval prompt state
  const [approvalPending, setApprovalPending] = useState<{
    toolCall: ToolCall;
    reason?: string;
    resolve: (approved: boolean) => void;
  } | null>(null);

  const appendEvent = useCallback((e: EventItem) => {
    setEvents((prev) => [...prev, e]);
  }, []);

  const updateLastAssistant = useCallback((text: string, streaming: boolean) => {
    setEvents((prev) => {
      const last = prev[prev.length - 1];
      if (last?.kind === "assistant") {
        return [...prev.slice(0, -1), { kind: "assistant", text, streaming }];
      }
      return [...prev, { kind: "assistant", text, streaming }];
    });
  }, []);

  // ── Streaming throttle — buffer chunks, flush at ~30 fps ─────────────────
  // Without this, setEvents() is called on every ~80-byte SSE chunk which causes
  // Ink to repaint the entire terminal tree at 80+ fps → visible flicker.
  const streamBufferRef = useRef<string>("");
  const flushTimerRef   = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopFlushTimer = useCallback(() => {
    if (flushTimerRef.current) {
      clearInterval(flushTimerRef.current);
      flushTimerRef.current = null;
    }
  }, []);

  const startFlushTimer = useCallback(() => {
    if (flushTimerRef.current) return; // already running
    flushTimerRef.current = setInterval(() => {
      const buf = streamBufferRef.current;
      if (!buf) return;
      streamBufferRef.current = "";
      setEvents((prev) => {
        const last = prev[prev.length - 1];
        if (last?.kind === "assistant" && last.streaming) {
          return [...prev.slice(0, -1), { kind: "assistant", text: last.text + buf, streaming: true }];
        }
        return [...prev, { kind: "assistant", text: buf, streaming: true }];
      });
    }, 33); // ~30 fps
  }, []);

  // Clean up on unmount (e.g. Ctrl+C while streaming)
  useEffect(() => () => stopFlushTimer(), []); // eslint-disable-line react-hooks/exhaustive-deps

  const updateLastTool = useCallback(
    (status: "running" | "done" | "denied" | "error", result?: string) => {
      setEvents((prev) => {
        const last = prev[prev.length - 1];
        if (last?.kind === "tool") {
          return [...prev.slice(0, -1), { ...last, status, result }];
        }
        return prev;
      });
    },
    []
  );

  // Show "no API key" warning on first render
  useEffect(() => {
    if (!config.apiKey) {
      appendEvent({
        kind: "system",
        text: "⚠️  No API key set. Run: atlas config set apiKey sk-atlas-xxx",
      });
    }
    if (workspace.hasAtlasMd || workspace.hasMemoryMd) {
      const ctxNote = [
        workspace.hasAtlasMd && "ATLAS.md",
        workspace.hasMemoryMd && "memory.md",
      ]
        .filter(Boolean)
        .join(" + ");
      appendEvent({ kind: "system", text: `📖 Loaded: ${ctxNote}` });
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Abort controller for Esc-to-interrupt
  const abortRef = useRef<AbortController | null>(null);

  // Queue: message typed while loading — auto-submitted when response finishes
  const pendingQueueRef = useRef<string | null>(null);
  // Stable ref to latest handleSubmit — avoids putting it in useEffect dep arrays
  // which causes Bun-minifier TDZ errors when the const is referenced before init.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const handleSubmitRef = useRef<(text: string) => void>(() => {});

  // Stable session ID for this run
  const sessionIdRef = useRef<string>(newSessionId());
  // Stable conversation UUID for this run — sent to backend so all turns
  // append to the same conversation row instead of creating a new one each turn.
  const conversationIdRef = useRef<string>(
    typeof crypto !== "undefined" && crypto.randomUUID
      ? crypto.randomUUID()
      : `${Date.now().toString(16)}-${Math.random().toString(16).slice(2, 10)}-4${Math.random().toString(16).slice(2, 5)}-8${Math.random().toString(16).slice(2, 5)}-${Math.random().toString(16).slice(2, 14)}`
  );

  // Ctrl+C to quit · Esc to interrupt current response
  useInput((input, key) => {
    if (key.ctrl && input === "c") exit();
    if (key.escape && loading) {
      abortRef.current?.abort();
      stopFlushTimer();
      const remaining = streamBufferRef.current;
      streamBufferRef.current = "";
      setEvents(prev => {
        const last = prev[prev.length - 1];
        if (last?.kind === "assistant" && last.streaming) {
          const text = stripToolBlocks(last.text + remaining).trim();
          return [
            ...prev.slice(0, -1),
            { kind: "assistant", text: text || "(interrupted)", streaming: false },
            { kind: "system", text: "⚠ Interrupted" },
          ];
        }
        return [...prev, { kind: "system", text: "⚠ Interrupted" }];
      });
      setLoading(false);
    }
  });

  // ── Agent event handler ──────────────────────────────────────────────────
  const handleAgentEvent = useCallback(
    async (event: AgentEvent): Promise<boolean | void> => {
      switch (event.type) {
        case "chunk": {
          const chunk = event.text ?? "";
          // Don't emit chunks that are only inside tool blocks
          const visible = stripToolBlocks(chunk);
          if (visible) {
            // Accumulate in a ref — the 30fps flush timer (startFlushTimer) will
            // batch these into a single setEvents() call per frame instead of
            // one per chunk, eliminating the flicker caused by ~80 repaints/sec.
            streamBufferRef.current += chunk;
            startFlushTimer();
          }
          break;
        }

        case "tool_call": {
          if (event.toolCall) {
            // Flush any buffered text, then finalise the streaming bubble
            stopFlushTimer();
            const remaining = streamBufferRef.current;
            streamBufferRef.current = "";
            setEvents((prev) => {
              const last = prev[prev.length - 1];
              const isStreaming = last?.kind === "assistant" && last.streaming;
              const allText = isStreaming ? last.text + remaining : remaining;
              const cleaned = stripToolBlocks(allText).trim();
              if (isStreaming) {
                const updated = cleaned
                  ? [...prev.slice(0, -1), { kind: "assistant" as const, text: cleaned, streaming: false }]
                  : prev.slice(0, -1);
                return [...updated, { kind: "tool", toolCall: event.toolCall!, status: "running" }];
              }
              // If there's pre-tool text that never flushed, add it before the tool
              const base = cleaned
                ? [...prev, { kind: "assistant" as const, text: cleaned, streaming: false }]
                : prev;
              return [...base, { kind: "tool", toolCall: event.toolCall!, status: "running" }];
            });
          }
          break;
        }

        case "tool_result": {
          updateLastTool("done", event.toolResult);
          break;
        }

        case "diff": {
          if (event.diff) appendEvent({ kind: "diff", diff: event.diff });
          break;
        }

        case "approval_needed": {
          if (!event.toolCall) return false;
          return new Promise<boolean>((resolve) => {
            setApprovalPending({ toolCall: event.toolCall!, reason: event.text, resolve });
          });
        }

        case "approval_denied": {
          updateLastTool("denied", event.text);
          break;
        }

        case "error": {
          setError(event.error);
          appendEvent({ kind: "error", text: event.error ?? "Unknown error" });
          break;
        }

        case "done": {
          // Flush any remaining buffered text, then finalise the streaming bubble.
          // Important: if the response was fast enough that the 33ms flush timer
          // never ticked, there is no existing streaming assistant event — in that
          // case we create one from the buffered text instead of silently dropping it.
          stopFlushTimer();
          const remaining = streamBufferRef.current;
          streamBufferRef.current = "";
          setEvents((prev) => {
            const last = prev[prev.length - 1];
            const isStreaming = last?.kind === "assistant" && last.streaming;
            const allText = isStreaming ? last.text + remaining : remaining;
            const finalText = stripToolBlocks(allText).trim();
            if (!finalText) return prev; // truly empty response — nothing to show
            if (isStreaming) {
              return [...prev.slice(0, -1), { ...last, text: finalText, streaming: false }];
            }
            // Timer never fired — create the event now
            return [...prev, { kind: "assistant" as const, text: finalText, streaming: false }];
          });
          if (event.usage) {
            setUsage(event.usage);
            // Rough cost estimate — varies by model but gives a useful ballpark
            const costPer1kIn  = 0.003;  // ~claude-3 sonnet rates
            const costPer1kOut = 0.015;
            const cost = (event.usage.inputTokens / 1000) * costPer1kIn
                       + (event.usage.outputTokens / 1000) * costPer1kOut;
            setTotalCost(prev => prev + cost);
          }
          setTurnCount(c => c + 1);
          break;
        }
      }
    },
    [appendEvent, updateLastTool, startFlushTimer, stopFlushTimer]
  );

  // ── Submit handler ────────────────────────────────────────────────────────
  const handleSubmit = useCallback(
    async (userText: string) => {
      if (loading) {
        // Queue the message — it will auto-send when the current response finishes
        pendingQueueRef.current = userText;
        return;
      }

      // ── Slash command ────────────────────────────────────────────────────
      if (userText.startsWith("/")) {
        const result = handleSlashCommand(userText, {
          workspace,
          permissions,
          audit,
          config,
          messages,
          customCommands,
        });

        switch (result.action) {
          case "print":
            appendEvent({ kind: "system", text: result.text ?? "" });
            return;

          case "clear":
            setMessages([]);
            setEvents([]);
            return;

          case "exit":
            exit();
            return;

          case "init": {
            setLoading(true);
            try {
              const res = await runAtlasInit(workspace.cwd, workspace);
              const lines = ["✓ Atlas workspace initialised", ...res.created.map((p) => `  + ${p}`)];
              appendEvent({ kind: "system", text: lines.join("\n") });
            } catch (e: unknown) {
              appendEvent({ kind: "error", text: String(e) });
            } finally {
              setLoading(false);
            }
            return;
          }

          case "set_model":
            if (result.model) {
              setConfig((c) => ({ ...c, model: result.model! }));
              appendEvent({ kind: "system", text: `Model set to ${result.model}` });
            }
            return;

          case "set_permissions":
            if (result.permissions) {
              const next = { ...permissions, ...result.permissions };
              setPermissions(next);
              if (workspace.atlasConfigDir) savePermissions(workspace.atlasConfigDir, next);
              appendEvent({ kind: "system", text: `Permission mode set to ${next.mode}` });
            }
            return;

          case "run_tool": {
            if (!result.toolName) return;
            const handler = toolHandlers[result.toolName];
            if (!handler) {
              appendEvent({ kind: "error", text: `Unknown tool: ${result.toolName}` });
              return;
            }
            setLoading(true);
            appendEvent({ kind: "tool", toolCall: { name: result.toolName, args: result.toolArgs ?? {} }, status: "running" });
            try {
              const res = await handler(result.toolArgs ?? {}, workspace.cwd);
              updateLastTool(res.success ? "done" : "error", res.output || res.error);
            } catch (e: unknown) {
              updateLastTool("error", String(e));
            } finally {
              setLoading(false);
            }
            return;
          }

          case "send":
            // Fall through with modified text
            if (result.text) {
              // Re-submit with generated text
              handleSubmit(result.text);
              return;
            }
            return;

          case "compact": {
            // Send a compact request, then reset history to just the summary
            handleSubmit(
              "Please write a concise summary of our conversation so far. Include: key decisions made, code changes applied, and any outstanding tasks. Be brief."
            );
            return;
          }

          case "add_files": {
            const pattern = result.text ?? "";
            if (!pattern) return;
            setLoading(true);
            try {
              const fs = await import("fs");
              const path = await import("path");
              const { glob } = await import("glob").catch(() => ({ glob: null }));

              // Collect matching files
              let files: string[] = [];
              if (glob) {
                files = await glob(pattern, { cwd: workspace.cwd, nodir: true });
              } else {
                // Fallback: treat as literal path
                const abs = path.resolve(workspace.cwd, pattern);
                if (fs.existsSync(abs)) files = [pattern];
              }

              if (files.length === 0) {
                appendEvent({ kind: "system", text: `No files matched: ${pattern}` });
                return;
              }

              // Read each file and inject as system messages
              let added = 0;
              for (const file of files.slice(0, 20)) { // cap at 20 files
                try {
                  const abs = path.resolve(workspace.cwd, file);
                  const content = fs.readFileSync(abs, "utf-8");
                  const ext = path.extname(file).slice(1) || "text";
                  const injection = `\`\`\`${ext}\n// File: ${file}\n${content}\n\`\`\``;
                  setMessages(prev => [...prev, { role: "user", content: `Context file: ${file}\n\n${injection}` }, { role: "assistant", content: `I've read ${file} and have it in context.` }]);
                  added++;
                } catch { /* skip unreadable files */ }
              }
              const extra = files.length > 20 ? ` (${files.length - 20} more skipped — cap is 20)` : "";
              appendEvent({ kind: "system", text: `📎 Added ${added} file${added === 1 ? "" : "s"} to context${extra}` });
            } catch (e: unknown) {
              appendEvent({ kind: "error", text: `Failed to add files: ${String(e)}` });
            } finally {
              setLoading(false);
            }
            return;
          }

          // ── F6: /fix loop ─────────────────────────────────────────────────────
          case "fix_loop": {
            if (loading) return;
            const rawCmd = result.text?.trim() || "";

            // Auto-detect test/build command from project type
            const detectCmd = async (): Promise<string> => {
              if (rawCmd) return rawCmd;
              try {
                const fs = await import("fs");
                const path = await import("path");
                const pkgPath = path.join(workspace.cwd, "package.json");
                if (fs.existsSync(pkgPath)) {
                  const pkg = JSON.parse(fs.readFileSync(pkgPath, "utf-8"));
                  if (pkg.scripts?.test) return "npm test";
                  if (pkg.scripts?.build) return "npm run build";
                }
                if (fs.existsSync(path.join(workspace.cwd, "Cargo.toml"))) return "cargo test";
                if (fs.existsSync(path.join(workspace.cwd, "go.mod"))) return "go test ./...";
                if (fs.existsSync(path.join(workspace.cwd, "pytest.ini")) ||
                    fs.existsSync(path.join(workspace.cwd, "pyproject.toml"))) return "pytest";
              } catch { /* ignore */ }
              return "npm test";
            };

            const fixCmd = await detectCmd();
            const MAX_FIX = 5;
            setLoading(true);

            let currentMessages = [...messages];
            try {
              for (let attempt = 1; attempt <= MAX_FIX; attempt++) {
                appendEvent({ kind: "system", text: `🔧 Attempt ${attempt}/${MAX_FIX} — running: ${fixCmd}` });

                const { spawnSync } = await import("child_process");
                const ran = spawnSync(fixCmd, {
                  shell: true, cwd: workspace.cwd, encoding: "utf8", timeout: 120_000,
                });
                const output = ((ran.stdout ?? "") + (ran.stderr ?? "")).trim();
                const exitCode = ran.status ?? 1;

                if (exitCode === 0) {
                  appendEvent({ kind: "system", text: `✓ All checks pass${attempt > 1 ? ` after ${attempt} fix${attempt === 1 ? "" : "es"}` : ""}!` });
                  setMessages(currentMessages);
                  break;
                }

                if (attempt === MAX_FIX) {
                  appendEvent({ kind: "error", text: `Still failing after ${MAX_FIX} attempts — manual fix needed.` });
                  break;
                }

                appendEvent({ kind: "system", text: `✗ Failed (exit ${exitCode}) — asking Atlas to fix…` });

                const fixPrompt =
                  `Command \`${fixCmd}\` failed with exit code ${exitCode}.\n\n` +
                  `**Output:**\n\`\`\`\n${output.slice(0, 3000)}\n\`\`\`\n\n` +
                  `Please fix the errors. Read the failing files, apply the fix, then I'll re-run.`;

                const abort = new AbortController();
                abortRef.current = abort;
                const { messages: updated } = await runAgentLoop({
                  config,
                  messages: [...currentMessages, { role: "user", content: fixPrompt }],
                  systemPrompt, permissions,
                  workspaceRoot: workspace.cwd, audit,
                  onEvent: handleAgentEvent, maxTurns: 8,
                  signal: abort.signal,
                  conversationId: conversationIdRef.current,
                });
                currentMessages = updated;
                setMessages(updated);
                if (abort.signal.aborted) break;
              }
            } finally {
              setLoading(false);
            }
            return;
          }

          // ── F7: /commit ────────────────────────────────────────────────────
          case "commit": {
            if (loading) return;
            // Check for staged changes first
            try {
              const { execSync } = await import("child_process");
              const staged = execSync("git diff --staged --stat", {
                cwd: workspace.cwd, encoding: "utf8", stdio: ["ignore", "pipe", "ignore"],
              }).trim();
              if (!staged) {
                appendEvent({ kind: "system", text: "Nothing staged. Run `git add <files>` first." });
                return;
              }
            } catch { /* not a git repo — let agent handle it */ }

            // Delegate to agent: generate + commit
            handleSubmit(
              "Generate a concise commit message for the staged changes " +
              "(use git_diff with staged:true to see them), then run: " +
              "git commit -m \"<message>\". Show the final commit hash."
            );
            return;
          }

          // ── F11: /rollback ─────────────────────────────────────────────────
          case "rollback": {
            const recent = audit.getRecent(20).filter(
              e => (e.tool === "write_file" || e.tool === "edit_file") && e.success
            );
            if (recent.length === 0) {
              appendEvent({ kind: "system", text: "No file changes recorded this session." });
              return;
            }

            const fs = await import("fs");
            const path = await import("path");
            const backupDir = workspace.atlasConfigDir
              ? path.join(workspace.atlasConfigDir, "backups")
              : null;

            let restored = 0;
            const skipped: string[] = [];

            for (const entry of [...recent].reverse().slice(0, 5)) {
              const filePath = String(entry.args.path ?? "");
              if (!filePath) continue;
              const backupPath = backupDir
                ? path.join(backupDir, filePath.replace(/[/\\]/g, "_"))
                : null;
              if (backupPath && fs.existsSync(backupPath)) {
                try {
                  const abs = path.resolve(workspace.cwd, filePath);
                  fs.copyFileSync(backupPath, abs);
                  restored++;
                  appendEvent({ kind: "system", text: `↩ Restored: ${filePath}` });
                } catch {
                  skipped.push(filePath);
                }
              } else {
                skipped.push(filePath);
              }
            }

            if (restored === 0) {
              appendEvent({ kind: "system", text: "No backups found. Backups are created in .atlas/backups/ during write/edit operations." });
            } else if (skipped.length > 0) {
              appendEvent({ kind: "system", text: `Skipped (no backup): ${skipped.join(", ")}` });
            }
            return;
          }

          // ── F12: /copy ─────────────────────────────────────────────────────
          case "copy_last": {
            const lastAssistant = [...events].reverse().find(e => e.kind === "assistant");
            if (!lastAssistant || lastAssistant.kind !== "assistant") {
              appendEvent({ kind: "system", text: "Nothing to copy yet." });
              return;
            }
            const text = lastAssistant.text;
            try {
              const { spawnSync } = await import("child_process");
              // Try platform clipboard commands
              const cmds = [
                ["pbcopy"],                          // macOS
                ["xclip", "-selection", "clipboard"], // Linux X11
                ["wl-copy"],                         // Linux Wayland
                ["clip"],                            // Windows
              ];
              let copied = false;
              for (const [bin, ...args] of cmds) {
                const r = spawnSync(bin, args, {
                  input: text, encoding: "utf8",
                  stdio: ["pipe", "ignore", "ignore"],
                  timeout: 3000,
                });
                if (r.status === 0) { copied = true; break; }
              }
              if (copied) {
                appendEvent({ kind: "system", text: `📋 Copied (${text.length} chars)` });
              } else {
                appendEvent({ kind: "system", text: "Clipboard not available — copy manually from above." });
              }
            } catch {
              appendEvent({ kind: "system", text: "Clipboard not available on this system." });
            }
            return;
          }

          // ── F10: /history ─────────────────────────────────────────────────
          case "history": {
            const sessions = listSessions(15);
            if (sessions.length === 0) {
              appendEvent({ kind: "system", text: "No saved sessions yet. Sessions are saved automatically after each turn." });
              return;
            }
            const lines = ["**Saved sessions** (newest first — use `/resume <n>` to restore):\n"];
            sessions.forEach((s, i) => {
              const date = new Date(s.timestamp).toLocaleString();
              const ws = s.workspace.split("/").slice(-2).join("/");
              const preview = s.preview.replace(/\n/g, " ").slice(0, 60);
              lines.push(`  **${i + 1}.** [${date}] ${ws} · ${s.turnCount} turn${s.turnCount === 1 ? "" : "s"}\n      _${preview}${preview.length >= 60 ? "…" : ""}_`);
            });
            appendEvent({ kind: "system", text: lines.join("\n") });
            return;
          }

          // ── F10: /resume <n> ──────────────────────────────────────────────
          case "resume_session": {
            const idx = parseInt(result.text ?? "1", 10) - 1;
            const sessions = listSessions(15);
            if (isNaN(idx) || idx < 0 || idx >= sessions.length) {
              appendEvent({ kind: "system", text: `Invalid session number. Use /history to see available sessions.` });
              return;
            }
            const session = loadSession(sessions[idx].id);
            if (!session) {
              appendEvent({ kind: "error", text: "Could not load session — file may have been deleted." });
              return;
            }
            setMessages(session.messages);
            setEvents([]);
            const date = new Date(session.timestamp).toLocaleString();
            appendEvent({
              kind: "system",
              text: `↩ Restored session from ${date} (${session.turnCount} turns, ${session.messages.length} messages)\n  Workspace: ${session.workspace}\n  Model: ${session.model}`,
            });
            // Replay the last user/assistant pair as visual context
            const lastPair = session.messages.filter(m => m.role === "user" || m.role === "assistant").slice(-2);
            for (const m of lastPair) {
              if (m.role === "user") appendEvent({ kind: "user", text: String(m.content ?? "") });
              if (m.role === "assistant") appendEvent({ kind: "assistant", text: String(m.content ?? ""), streaming: false });
            }
            return;
          }

          case "save_memory": {
            if (!workspace.atlasConfigDir || messages.length < 2) {
              appendEvent({ kind: "system", text: "Nothing to save yet — have a conversation first." });
              return;
            }
            appendEvent({ kind: "system", text: "💾 Saving memory…" });
            const MEMORY_PROMPT =
              "Summarise our conversation so far into a concise project memory update. " +
              "Include: key decisions made, files changed, current task status, and any " +
              "important context a future session should know. " +
              "Format as clean markdown. Keep it under 400 words. No preamble.";
            const memMessages: Message[] = [
              ...messages,
              { role: "user", content: MEMORY_PROMPT },
            ];
            const atlasDir = workspace.atlasConfigDir;
            const ts = new Date().toISOString().replace("T", " ").slice(0, 16);
            completeSync(config, memMessages)
              .then(({ text }) => {
                if (text.trim()) {
                  saveMemory(atlasDir, `# Atlas Memory\n_Last updated: ${ts}_\n\n${text.trim()}\n`);
                  appendEvent({ kind: "system", text: "✓ Memory saved to .atlas/memory.md" });
                }
              })
              .catch((e: unknown) => {
                appendEvent({ kind: "error", text: `Memory save failed: ${String(e)}` });
              });
            return;
          }
        }
        return;
      }

      // ── Regular message or agentic loop ───────────────────────────────────
      setLoading(true);
      setError(undefined);

      // F9 — Auto-context: scan message for file paths → silently pre-inject
      const autoContextMessages: Message[] = [];
      try {
        const fs = await import("fs");
        const path = await import("path");
        // Match paths like src/auth.ts, ./config.py, auth.ts — with common code extensions
        const pathRe = /(?:^|[\s"'`(])((\.{0,2}\/)?[\w./-]+\.(?:ts|tsx|js|jsx|py|go|rs|java|kt|cs|cpp|c|h|rb|php|swift|yaml|yml|json|toml|md|sh|bash))\b/g;
        const seen = new Set<string>();
        let m: RegExpExecArray | null;
        while ((m = pathRe.exec(userText)) !== null) {
          const candidate = m[1].trim();
          if (seen.has(candidate)) continue;
          seen.add(candidate);
          const abs = path.resolve(workspace.cwd, candidate);
          if (fs.existsSync(abs) && fs.statSync(abs).isFile()) {
            try {
              const content = fs.readFileSync(abs, "utf-8");
              const ext = path.extname(candidate).slice(1);
              autoContextMessages.push(
                { role: "user",      content: `[Auto-context] ${candidate}:\n\`\`\`${ext}\n${content.slice(0, 8000)}\n\`\`\`` },
                { role: "assistant", content: `I have read ${candidate} and have it in context.` }
              );
            } catch { /* unreadable — skip */ }
          }
        }
      } catch { /* fs errors — skip auto-context silently */ }

      const userMsg: Message = { role: "user", content: userText };
      let nextMessages = [...messages, ...autoContextMessages, userMsg];
      setMessages(nextMessages);
      appendEvent({ kind: "user", text: userText });

      // F13 — Auto-compact: if context is getting large, summarise first
      const TOKEN_LIMIT = 80_000;
      const estTokens = nextMessages.reduce(
        (sum, m) => sum + Math.ceil(String(m.content ?? "").length / 4), 0
      );
      if (estTokens > TOKEN_LIMIT && nextMessages.length > 12) {
        appendEvent({
          kind: "system",
          text: `📦 Context ~${Math.round(estTokens / 1000)}K tokens — auto-compacting…`,
        });
        try {
          const { text: summary } = await completeSync(config, [
            ...nextMessages,
            {
              role: "user",
              content:
                "Summarise this entire conversation in 400 words: key decisions made, " +
                "code changes applied, current state, and any open tasks. Be dense and precise.",
            },
          ]);
          if (summary.trim()) {
            // Keep: summary bubble + last 6 messages + new user message
            const tail = nextMessages
              .filter(m => m.role !== "system")
              .slice(-6);
            nextMessages = [
              { role: "user",      content: "**Conversation summary (auto-compacted):**" },
              { role: "assistant", content: summary.trim() },
              ...tail,
            ];
            setMessages(nextMessages);
            appendEvent({
              kind: "system",
              text: `✓ Compacted — continuing with summary + last ${tail.length} messages`,
            });
          }
        } catch { /* if compact fails, proceed with full context */ }
      }

      // Fresh abort controller for this turn
      const abort = new AbortController();
      abortRef.current = abort;

      try {
        if (agentEnabled) {
          const { messages: updatedMessages, usage: u } = await runAgentLoop({
            config,
            messages: nextMessages,
            systemPrompt,
            permissions,
            workspaceRoot: workspace.cwd,
            audit,
            autoRoute: autoModel,
            onEvent: handleAgentEvent,
            maxTurns: 15,
            signal: abort.signal,
            conversationId: conversationIdRef.current,
          });
          setMessages(updatedMessages);
          if (u) setUsage(u);
        } else {
          // Plain chat (no tools) — throttle to ~30fps to avoid flicker
          let fullText = "";
          let lastFlush = 0;
          const sysMessages: Message[] = [{ role: "system", content: systemPrompt }, ...nextMessages];
          for await (const event of streamCompletion(config, sysMessages, undefined, abort.signal, conversationIdRef.current)) {
            if (event.type === "text") {
              fullText += event.content;
              const now = Date.now();
              if (now - lastFlush >= 33) {
                lastFlush = now;
                updateLastAssistant(fullText, true);
              }
            } else if (event.type === "usage") {
              setUsage(event.usage);
            }
          }
          updateLastAssistant(fullText, false); // final flush
          setMessages([...nextMessages, { role: "assistant", content: fullText }]);
        }
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : String(e);
        setError(msg);
        appendEvent({ kind: "error", text: msg });
      } finally {
        setLoading(false);
      }
    },
    [loading, messages, config, permissions, workspace, agentEnabled, autoModel, audit, customCommands, systemPrompt, handleAgentEvent, appendEvent, updateLastAssistant, updateLastTool, exit]
  );

  // Keep the ref in sync with the latest handleSubmit (no dep-array issues)
  handleSubmitRef.current = handleSubmit;

  // ── Queue fire — uses ref so handleSubmit never appears in a dep array ────
  useEffect(() => {
    if (!loading) {
      const queued = pendingQueueRef.current;
      if (queued) {
        pendingQueueRef.current = null;
        setTimeout(() => handleSubmitRef.current(queued), 80);
      }
    }
  }, [loading]); // ← only loading; handleSubmit accessed via stable ref

  // ── Render ────────────────────────────────────────────────────────────────
  // Short workspace path for the banner
  const shortWs = (() => {
    const parts = workspace.cwd.replace(/\\/g, "/").split("/").filter(Boolean);
    return parts.slice(-2).join("/");
  })();

  // Switch to compact header once the user has had their first turn
  const compactHeader = turnCount > 0 || events.some(e => e.kind === "user");

  // ── Render one event item (used by both <Static> and the live tail) ────
  // Each item is keyed by index because EventItem has no stable id; once an
  // item is rendered in <Static> it is never re-rendered, so this is safe.
  const renderEvent = (e: EventItem, i: number) => {
    switch (e.kind) {
      case "user":
        return (
          <Box key={`u-${i}`} flexDirection="column" marginTop={1} marginBottom={0}>
            {i > 0 && (
              <Text dimColor>{"─".repeat(process.stdout.columns ? Math.min(process.stdout.columns - 2, 80) : 60)}</Text>
            )}
            <Box gap={1} alignItems="flex-start">
              <Text color="cyan" bold>▶</Text>
              <Text color="cyan" bold>you</Text>
            </Box>
            <Box marginLeft={3}>
              <Text>{e.text}</Text>
            </Box>
          </Box>
        );
      case "assistant":
        return (
          <Box key={`a-${i}`} flexDirection="column" marginTop={1} marginBottom={0}>
            <Box gap={1} alignItems="flex-start">
              <Text color="green" bold>◆</Text>
              <Text color="green" bold>atlas</Text>
              {e.streaming && <Text color="green" dimColor>…</Text>}
            </Box>
            <Box marginLeft={3} flexDirection="column">
              {e.streaming ? (
                <Text>
                  {e.text || ""}
                  <Text color="green">▌</Text>
                </Text>
              ) : (
                <MarkdownRenderer content={e.text || ""} />
              )}
            </Box>
          </Box>
        );
      case "tool":
        return (
          <Box key={`t-${i}`} marginLeft={3} marginTop={0}>
            <ToolCallDisplay toolCall={e.toolCall} status={e.status} result={e.result} />
          </Box>
        );
      case "diff":
        return (
          <Box key={`d-${i}`} marginLeft={2}>
            <DiffViewer diff={e.diff} />
          </Box>
        );
      case "system":
        return (
          <Box key={`s-${i}`} marginTop={1}>
            <Text dimColor>ℹ {e.text}</Text>
          </Box>
        );
      case "error":
        return (
          <Box key={`e-${i}`} marginTop={1}>
            <Box borderStyle="round" borderColor="red" paddingX={1}>
              <Text color="red" bold>✗ </Text>
              <Text color="red">{e.text}</Text>
            </Box>
          </Box>
        );
    }
    return null;
  };

  // ── Flicker fix: split events into "static" (frozen, written once via
  //    Ink's <Static>) and "live" (the currently streaming assistant or a
  //    tool whose status may still change). <Static> never repaints, so the
  //    transcript no longer flickers when streaming chunks arrive. ──
  let liveStart = events.length;
  if (events.length > 0) {
    const last = events[events.length - 1];
    const isLive =
      (last?.kind === "assistant" && last.streaming) ||
      (last?.kind === "tool" && last.status === "running");
    if (isLive) liveStart = events.length - 1;
  }
  const staticEvents = events.slice(0, liveStart);
  const liveEvents   = events.slice(liveStart);

  return (
    <Box flexDirection="column">
      {/* ── Header: big banner on start, compact after first turn ── */}
      <Banner
        model={config.model}
        baseUrl={config.baseUrl}
        workspace={shortWs}
        compact={compactHeader}
        agentEnabled={agentEnabled}
      />

      {/* ── Static event log — written once per item, never repainted ── */}
      <Static items={staticEvents.map((e, i) => ({ e, i }))}>
        {({ e, i }) => (
          <Box key={`st-${i}`} paddingX={1} flexDirection="column">
            {renderEvent(e, i)}
          </Box>
        )}
      </Static>

      {/* ── Live tail — only the currently-streaming bubble re-renders ── */}
      <Box flexDirection="column" paddingX={1}>
        {liveEvents.map((e, idx) => renderEvent(e, liveStart + idx))}

        {/* V3 — Thinking indicator: shown when loading but no streaming bubble yet */}
        {loading && !events.some(e => e.kind === "assistant" && e.streaming) && (
          <Box marginTop={1} gap={1} marginLeft={0}>
            <Text color="green" bold>◆</Text>
            <Text color="green" bold>atlas</Text>
            <Text color="green"><Spinner type="dots" /></Text>
            <Text color="green" dimColor>thinking</Text>
          </Box>
        )}

        {/* Empty state — only show before any events AND without the big banner (which already shows tips) */}
        {events.length === 0 && compactHeader && (
          <Box marginTop={1}>
            <Text dimColor>Type a message to start. /help for commands.</Text>
          </Box>
        )}
      </Box>

      {/* ── Approval prompt (modal) ── */}
      {approvalPending && (
        <ApprovalPrompt
          toolCall={approvalPending.toolCall}
          reason={approvalPending.reason}
          onApprove={() => {
            const resolve = approvalPending.resolve;
            setApprovalPending(null);
            resolve(true);
          }}
          onDeny={() => {
            const resolve = approvalPending.resolve;
            setApprovalPending(null);
            resolve(false);
          }}
        />
      )}

      {/* ── Mascot — only in empty/welcome state; its animation timer
           lives inside the component so re-renders are isolated and do
           NOT cause the whole app tree to flicker. ── */}
      {events.length === 0 && <Mascot state={mState} />}

      {/* ── Status bar ── */}
      <StatusBar
        model={config.model}
        baseUrl={config.baseUrl}
        loading={loading}
        usage={usage}
        totalCost={totalCost}
        error={error}
        permissionMode={permissions.mode}
        cwd={workspace.cwd}
        turnCount={turnCount}
        gitBranch={gitBranch}
      />

      {/* ── Input (always active for typing; submit queues if loading) ── */}
      <InputBox
        onSubmit={handleSubmit}
        disabled={!!approvalPending}
        placeholder={
          loading
            ? "Composing… (↵ queues for after response · Esc interrupt)"
            : agentEnabled
              ? "Message Atlas… (↑↓ history · Shift+Enter newline · Esc interrupt · /help)"
              : "Message Atlas… (plain chat · Shift+Enter newline · Esc interrupt)"
        }
      />
    </Box>
  );
}

// ---------------------------------------------------------------------------
// Root — handles first-run setup then renders App
// ---------------------------------------------------------------------------

interface RootProps {
  initialConfig: AtlasConfig;
  workspace: WorkspaceInfo;
  agentEnabled: boolean;
  autoModel: boolean;
  systemPromptOverride?: string;
}

function Root({ initialConfig, workspace, agentEnabled, autoModel, systemPromptOverride }: RootProps) {
  const [config, setConfig] = useState<AtlasConfig>(initialConfig);
  const [ready, setReady] = useState(!!initialConfig.apiKey);

  const permissions    = loadPermissions(workspace.atlasConfigDir);
  const projectContext = loadProjectContext(workspace);
  const customCommands = loadCustomCommands(workspace.atlasConfigDir);
  const audit          = new AuditLog(workspace.atlasConfigDir);
  const systemPrompt   = systemPromptOverride ?? buildSystemPrompt(projectContext, workspace.cwd);

  if (!ready) {
    return (
      <SetupScreen
        defaultBaseUrl={config.baseUrl}
        onComplete={(apiKey, baseUrl) => {
          setConfig((c) => ({ ...c, apiKey, baseUrl }));
          setReady(true);
        }}
      />
    );
  }

  return (
    <App
      config={config}
      workspace={workspace}
      permissions={permissions}
      systemPrompt={systemPrompt}
      agentEnabled={agentEnabled}
      autoModel={autoModel}
      customCommands={customCommands}
      audit={audit}
    />
  );
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------

(async () => {
  // Handle non-TUI commands first
  if (await handleCLICommand()) process.exit(0);

  // ── Stdin pipe mode: cat error.log | atlas "what's wrong?" ──────────────
  if (!process.stdin.isTTY) {
    const chunks: Buffer[] = [];
    for await (const chunk of process.stdin) chunks.push(chunk as Buffer);
    const stdinContent = Buffer.concat(chunks).toString("utf-8").trim();

    if (stdinContent) {
      const cfg = loadConfig();
      const config: AtlasConfig = {
        apiKey:  cli.flags.key   ?? cfg.apiKey,
        baseUrl: cli.flags.url   ?? cfg.baseUrl,
        model:   cli.flags.model ?? cfg.model,
        stream:  cfg.stream,
      };
      // If a prompt was also passed as positional arg, combine; otherwise ask to analyse
      const extraPrompt = cli.input.join(" ").trim();
      const prompt = extraPrompt
        ? `${extraPrompt}\n\n${stdinContent}`
        : `Analyse the following:\n\n${stdinContent}`;
      try {
        const { streamCompletion } = await import("./api.js");
        const msgs: Message[] = [{ role: "user", content: prompt }];
        for await (const event of streamCompletion(config, msgs)) {
          if (event.type === "text") process.stdout.write(event.content);
        }
        process.stdout.write("\n");
      } catch (e: unknown) {
        console.error("Error:", e instanceof Error ? e.message : String(e));
        process.exit(1);
      }
      process.exit(0);
    }
  }

  // Fire-and-forget background update check (never blocks startup, never crashes)
  try { checkForUpdates(); } catch { /* updater must never crash CLI */ }

  // Detect workspace + load config
  const [workspace, persisted] = await Promise.all([
    detectWorkspace(),
    Promise.resolve(loadConfig()),
  ]);

  const config: AtlasConfig = {
    apiKey:  cli.flags.key   ?? persisted.apiKey,
    baseUrl: cli.flags.url   ?? persisted.baseUrl,
    model:   cli.flags.model ?? persisted.model,
    stream:  persisted.stream,
  };

  render(
    <Root
      initialConfig={config}
      workspace={workspace}
      agentEnabled={cli.flags.agent}
      autoModel={cli.flags.autoModel}
      systemPromptOverride={cli.flags.system}
    />,
    { patchConsole: false }
  );
})();
