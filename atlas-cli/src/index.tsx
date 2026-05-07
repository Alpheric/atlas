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
import { render, Box, Text, useApp, useInput } from "ink";
import meow from "meow";

import { loadConfig, saveConfig, getConfigPath, AtlasConfig } from "./config.js";
import { SetupScreen } from "./components/SetupScreen.js";
import { Message, streamCompletion, UsageInfo, stripToolBlocks } from "./api.js";
import { MessageList } from "./components/MessageList.js";
import { InputBox } from "./components/InputBox.js";
import { StatusBar } from "./components/StatusBar.js";
import { ApprovalPrompt } from "./components/ApprovalPrompt.js";
import { ToolCallDisplay } from "./components/ToolCallDisplay.js";
import { DiffViewer } from "./components/DiffViewer.js";
import { Banner } from "./components/Banner.js";

import { detectWorkspace, WorkspaceInfo } from "./workspace.js";
import { checkForUpdates } from "./updater.js";
import { loadProjectContext, loadCustomCommands } from "./project-context.js";
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
    config             Show config
    config set <k> <v> Set a config value
    init               Initialise .atlas/ workspace
    doctor             Check configuration health
    diff               Show git diff
    git                Show git status
    review             Review staged changes
    commit-message     Generate a commit message

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

    if (cfg.apiKey) ok.push("✓  API key configured");
    else issues.push("⚠️  No API key — run: atlas config set apiKey sk-atlas-xxx");

    if (workspace.isGit) ok.push("✓  Git repository detected");
    else issues.push("ℹ️  Not a git repository");

    if (workspace.hasAtlasMd) ok.push("✓  ATLAS.md present");
    else issues.push("ℹ️  No ATLAS.md — run: atlas init");

    if (workspace.hasMemoryMd) ok.push("✓  .atlas/memory.md present");
    else issues.push("ℹ️  No memory file — run: atlas init");

    [...ok, ...(issues.length ? ["", "Issues:"] : []), ...issues].forEach((l) => console.log(l));
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
  const [error, setError] = useState<string | undefined>();
  // Number of completed user→assistant turns (drives compact header threshold)
  const [turnCount, setTurnCount] = useState(0);

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

  // Ctrl+C to quit
  useInput((input, key) => {
    if (key.ctrl && input === "c") exit();
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
            setEvents((prev) => {
              const last = prev[prev.length - 1];
              if (last?.kind === "assistant" && last.streaming) {
                return [
                  ...prev.slice(0, -1),
                  { kind: "assistant", text: last.text + chunk, streaming: true },
                ];
              }
              return [...prev, { kind: "assistant", text: chunk, streaming: true }];
            });
          }
          break;
        }

        case "tool_call": {
          if (event.toolCall) {
            // Finalise the streaming assistant bubble (strip tool block from display)
            setEvents((prev) => {
              const last = prev[prev.length - 1];
              if (last?.kind === "assistant" && last.streaming) {
                const cleaned = stripToolBlocks(last.text).trim();
                const updated = cleaned
                  ? [{ ...last, text: cleaned, streaming: false }]
                  : prev.slice(0, -1);
                return [...updated, { kind: "tool", toolCall: event.toolCall!, status: "running" }];
              }
              return [...prev, { kind: "tool", toolCall: event.toolCall!, status: "running" }];
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
          // Finalise last streaming bubble
          setEvents((prev) => {
            const last = prev[prev.length - 1];
            if (last?.kind === "assistant" && last.streaming) {
              return [
                ...prev.slice(0, -1),
                { ...last, text: stripToolBlocks(last.text).trim(), streaming: false },
              ];
            }
            return prev;
          });
          if (event.usage) setUsage(event.usage);
          setTurnCount(c => c + 1);
          break;
        }
      }
    },
    [appendEvent, updateLastTool]
  );

  // ── Submit handler ────────────────────────────────────────────────────────
  const handleSubmit = useCallback(
    async (userText: string) => {
      if (loading) return;

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
        }
        return;
      }

      // ── Regular message or agentic loop ───────────────────────────────────
      setLoading(true);
      setError(undefined);

      const userMsg: Message = { role: "user", content: userText };
      const nextMessages = [...messages, userMsg];
      setMessages(nextMessages);
      appendEvent({ kind: "user", text: userText });

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
          });
          setMessages(updatedMessages);
          if (u) setUsage(u);
        } else {
          // Plain chat (no tools)
          let fullText = "";
          const sysMessages: Message[] = [{ role: "system", content: systemPrompt }, ...nextMessages];
          for await (const event of streamCompletion(config, sysMessages)) {
            if (event.type === "text") {
              fullText += event.content;
              updateLastAssistant(fullText, true);
            } else if (event.type === "usage") {
              setUsage(event.usage);
            }
          }
          updateLastAssistant(fullText, false);
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

  // ── Render ────────────────────────────────────────────────────────────────
  // Short workspace path for the banner
  const shortWs = (() => {
    const parts = workspace.cwd.replace(/\\/g, "/").split("/").filter(Boolean);
    return parts.slice(-2).join("/");
  })();

  // Switch to compact header once the user has had their first turn
  const compactHeader = turnCount > 0 || events.some(e => e.kind === "user");

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

      {/* ── Event log ── */}
      <Box flexDirection="column" paddingX={1}>
        {events.map((e, i) => {
          switch (e.kind) {
            case "user":
              return (
                <Box key={i} flexDirection="column" marginTop={1} marginBottom={0}>
                  {/* Turn separator (not on the first user message) */}
                  {i > 0 && <Text dimColor>{"─".repeat(40)}</Text>}
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
                <Box key={i} flexDirection="column" marginTop={1} marginBottom={0}>
                  <Box gap={1} alignItems="flex-start">
                    <Text color="green" bold>◆</Text>
                    <Text color="green" bold>atlas</Text>
                    {e.streaming && <Text color="green" dimColor>…</Text>}
                  </Box>
                  <Box marginLeft={3}>
                    <Text>
                      {e.text || ""}
                      {e.streaming ? <Text color="green">▌</Text> : null}
                    </Text>
                  </Box>
                </Box>
              );

            case "tool":
              return (
                <Box key={i} marginLeft={3} marginTop={0}>
                  <ToolCallDisplay
                    toolCall={e.toolCall}
                    status={e.status}
                    result={e.result}
                  />
                </Box>
              );

            case "diff":
              return (
                <Box key={i} marginLeft={2}>
                  <DiffViewer diff={e.diff} />
                </Box>
              );

            case "system":
              return (
                <Box key={i} marginTop={1}>
                  <Text dimColor>ℹ {e.text}</Text>
                </Box>
              );

            case "error":
              return (
                <Box key={i} marginTop={1}>
                  <Box borderStyle="round" borderColor="red" paddingX={1}>
                    <Text color="red" bold>✗ </Text>
                    <Text color="red">{e.text}</Text>
                  </Box>
                </Box>
              );
          }
        })}

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

      {/* ── Status bar ── */}
      <StatusBar
        model={config.model}
        baseUrl={config.baseUrl}
        loading={loading}
        usage={usage}
        error={error}
        permissionMode={permissions.mode}
        cwd={workspace.cwd}
        turnCount={turnCount}
      />

      {/* ── Input ── */}
      <InputBox
        onSubmit={handleSubmit}
        disabled={loading || !!approvalPending}
        placeholder={
          agentEnabled
            ? "Message Atlas… (↑↓ history · /help for commands)"
            : "Message Atlas… (plain chat, no tools)"
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

  // Fire-and-forget background update check (never blocks startup)
  checkForUpdates();

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
