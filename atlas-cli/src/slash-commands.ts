/**
 * Slash command registry.
 *
 * Commands are recognised when the user's input starts with "/".
 * Each command returns a SlashResult describing what should happen.
 */

import { WorkspaceInfo } from "./workspace.js";
import { PermissionConfig, PermissionMode } from "./permissions.js";
import { AuditLog } from "./audit.js";
import { AtlasConfig } from "./config.js";
import { MODEL_DESCRIPTIONS } from "./model-router.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type SlashAction =
  | "none"            // command handled silently (no further action)
  | "print"           // print text to the conversation
  | "clear"           // clear message history
  | "send"            // send modified/generated text to the model
  | "set_model"       // change the active model
  | "set_permissions" // change permission mode
  | "compact"         // compact / summarise conversation
  | "exit"            // exit the CLI
  | "init"            // run atlas init
  | "run_tool"        // directly invoke a tool
  | "save_memory"     // force an immediate memory save
  | "add_files"       // inject files into context
  | "fix_loop"        // run build/test → fix errors → repeat
  | "commit"          // generate message + git commit
  | "pr"              // generate PR description
  | "rollback"        // undo last file changes
  | "copy_last"       // copy last response to clipboard
  | "history"         // list saved sessions
  | "resume_session"; // restore a saved session by index

export interface SlashResult {
  action: SlashAction;
  text?: string;       // for "print" or "send"
  model?: string;      // for "set_model"
  permissions?: Partial<PermissionConfig>; // for "set_permissions"
  toolName?: string;   // for "run_tool"
  toolArgs?: Record<string, unknown>;
  error?: string;
}

export interface SlashContext {
  workspace: WorkspaceInfo;
  permissions: PermissionConfig;
  audit: AuditLog;
  config: AtlasConfig;
  messages: Array<{ role: string; content: string | null }>;
  customCommands: Record<string, string>;
}

// ---------------------------------------------------------------------------
// Help text
// ---------------------------------------------------------------------------

const HELP_TEXT = `
**Atlas Code CLI — Slash Commands**

**Workspace**
  /init           Initialise .atlas/ and ATLAS.md in the current directory
  /status         Show workspace, model, permissions, token usage
  /doctor         Check configuration health

**Chat**
  /help           Show this help
  /clear          Clear conversation history
  /compact        Summarise conversation to reduce context

**Model**
  /model [name]   Show or set the active model
  /permissions [mode]  Show or set permission mode (readonly/ask/auto/danger)

**Files**
  /read <path>    Read a file and show its contents
  /add <glob>     Inject file(s) into context (e.g. /add src/**/*.ts)
  /edit <path>    Open a file for editing
  /write <path>   Write a new file
  /rollback       Undo last file changes (from .atlas/backups/)

**Code**
  /run <cmd>      Run a shell command
  /test           Run tests
  /build          Run build
  /fix [cmd]      Run tests/build → auto-fix errors → repeat (up to 5×)

**Git**
  /git            Show git status
  /diff           Show git diff
  /review         Review staged changes
  /commit         Generate commit message + git commit
  /pr [base]      Generate PR description (default base: main)

**Memory & Sessions**
  /memory         Show .atlas/memory.md
  /memory save    Force immediate memory save
  /copy           Copy last response to clipboard
  /history        List saved sessions
  /resume <n>     Restore session n from /history

**Planning**
  /plan <goal>    Create a step-by-step implementation plan

**Analysis**
  /security       Security review of the codebase
  /docs <path>    Generate documentation for a file
  /undo           Show recent tool actions (for manual undo)

**Custom commands** (from .atlas/commands/*.md)
  /<name> [args]  Run a custom command

Type **Ctrl+C** to quit.
`.trim();

// ---------------------------------------------------------------------------
// Command router
// ---------------------------------------------------------------------------

export function parseSlashCommand(input: string): { name: string; args: string } | null {
  if (!input.startsWith("/")) return null;
  const [rawName, ...rest] = input.slice(1).trim().split(/\s+/);
  return { name: rawName.toLowerCase(), args: rest.join(" ") };
}

export function handleSlashCommand(
  input: string,
  ctx: SlashContext
): SlashResult {
  const parsed = parseSlashCommand(input);
  if (!parsed) return { action: "none" };

  const { name, args } = parsed;

  switch (name) {
    // ── Help ──────────────────────────────────────────────────────────────
    case "help":
      return { action: "print", text: HELP_TEXT };

    // ── Init ──────────────────────────────────────────────────────────────
    case "init":
      return { action: "init" };

    // ── Status ────────────────────────────────────────────────────────────
    case "status": {
      const w = ctx.workspace;
      const lines: string[] = [
        "**Atlas Status**",
        `- CWD: ${w.cwd}`,
        `- Model: ${ctx.config.model}`,
        `- Base URL: ${ctx.config.baseUrl}`,
        `- Permissions: ${ctx.permissions.mode}`,
        `- Git: ${w.isGit ? "yes (" + (w.gitRoot ?? w.cwd) + ")" : "no"}`,
        `- Framework: ${w.framework ?? "not detected"}`,
        `- Package manager: ${w.packageManager ?? "not detected"}`,
        `- ATLAS.md: ${w.hasAtlasMd ? "yes" : "no"}`,
        `- Memory: ${w.hasMemoryMd ? "yes" : "no"}`,
        `- Messages in session: ${ctx.messages.length}`,
      ];
      return { action: "print", text: lines.join("\n") };
    }

    // ── Doctor ────────────────────────────────────────────────────────────
    case "doctor": {
      const issues: string[] = [];
      const ok: string[] = [];

      if (!ctx.config.apiKey) issues.push("⚠️  No API key set — run: atlas config set apiKey sk-atlas-xxx");
      else ok.push("✓  API key configured");

      if (!ctx.workspace.hasAtlasMd) issues.push("ℹ️  No ATLAS.md found — run /init to create one");
      else ok.push("✓  ATLAS.md present");

      if (!ctx.workspace.hasMemoryMd) issues.push("ℹ️  No .atlas/memory.md — run /init to create one");
      else ok.push("✓  .atlas/memory.md present");

      if (!ctx.workspace.isGit) issues.push("ℹ️  Not a git repository");
      else ok.push("✓  Git repository detected");

      const lines = [...ok, ...(issues.length ? ["", "**Issues:**", ...issues] : [])];
      return { action: "print", text: lines.join("\n") };
    }

    // ── Clear ─────────────────────────────────────────────────────────────
    case "clear":
      return { action: "clear" };

    // ── Compact ───────────────────────────────────────────────────────────
    case "compact":
      return {
        action: "send",
        text: "Please summarise our conversation so far into a concise context summary. Focus on key decisions, code changes, and outstanding tasks. This summary will replace the conversation history.",
      };

    // ── Model ─────────────────────────────────────────────────────────────
    case "model": {
      if (!args) {
        const lines = ["**Available models:**"];
        for (const [m, desc] of Object.entries(MODEL_DESCRIPTIONS)) {
          const active = m === ctx.config.model ? " ← current" : "";
          lines.push(`  ${m}${active} — ${desc}`);
        }
        lines.push("\nUsage: /model <name>");
        return { action: "print", text: lines.join("\n") };
      }
      if (!MODEL_DESCRIPTIONS[args]) {
        return {
          action: "print",
          text: `Unknown model: ${args}\nValid models: ${Object.keys(MODEL_DESCRIPTIONS).join(", ")}`,
        };
      }
      return { action: "set_model", model: args };
    }

    // ── Permissions ───────────────────────────────────────────────────────
    case "permissions":
    case "perms": {
      const modes: PermissionMode[] = ["readonly", "ask", "auto", "danger"];
      if (!args) {
        const lines = [
          `**Permission mode:** ${ctx.permissions.mode}`,
          "",
          "**Modes:**",
          "  readonly — only reads allowed",
          "  ask      — ask before every write/run (default)",
          "  auto     — allow reads + edits, ask for shell commands",
          "  danger   — allow all except explicit deny list",
          "",
          "Usage: /permissions <mode>",
          "",
          `**Allow list:** ${ctx.permissions.allow.join(", ")}`,
          `**Ask list:** ${ctx.permissions.ask.join(", ")}`,
          `**Deny list:** ${ctx.permissions.deny.join(", ")}`,
        ];
        return { action: "print", text: lines.join("\n") };
      }
      if (!modes.includes(args as PermissionMode)) {
        return { action: "print", text: `Invalid mode: ${args}. Valid: ${modes.join(", ")}` };
      }
      return { action: "set_permissions", permissions: { mode: args as PermissionMode } };
    }

    // ── Read ──────────────────────────────────────────────────────────────
    case "read": {
      if (!args) return { action: "print", text: "Usage: /read <path>" };
      return { action: "run_tool", toolName: "read_file", toolArgs: { path: args } };
    }

    // ── Add file(s) to context ────────────────────────────────────────────
    case "add": {
      if (!args) return { action: "print", text: "Usage: /add <file|glob>  — inject file(s) into context" };
      return { action: "add_files", text: args };
    }

    // ── List ──────────────────────────────────────────────────────────────
    case "ls":
    case "list": {
      return {
        action: "run_tool",
        toolName: "list_files",
        toolArgs: { path: args || "." },
      };
    }

    // ── Edit ──────────────────────────────────────────────────────────────
    case "edit": {
      if (!args) return { action: "print", text: "Usage: /edit <path>" };
      return {
        action: "send",
        text: `Please read the file at \`${args}\` and then help me edit it. Describe what you want to change and I'll apply the edits.`,
      };
    }

    // ── Write ─────────────────────────────────────────────────────────────
    case "write": {
      if (!args) return { action: "print", text: "Usage: /write <path>" };
      return {
        action: "send",
        text: `I want to write a new file at \`${args}\`. Please help me create its content.`,
      };
    }

    // ── Run ───────────────────────────────────────────────────────────────
    case "run": {
      if (!args) return { action: "print", text: "Usage: /run <command>" };
      return { action: "run_tool", toolName: "run_command", toolArgs: { command: args } };
    }

    // ── Test ──────────────────────────────────────────────────────────────
    case "test": {
      const pm = ctx.workspace.packageManager ?? "npm";
      return { action: "run_tool", toolName: "run_command", toolArgs: { command: `${pm} test` } };
    }

    // ── Build ─────────────────────────────────────────────────────────────
    case "build": {
      const pm = ctx.workspace.packageManager ?? "npm";
      return { action: "run_tool", toolName: "run_command", toolArgs: { command: `${pm} run build` } };
    }

    // ── Git ───────────────────────────────────────────────────────────────
    case "git": {
      return { action: "run_tool", toolName: "git_status", toolArgs: {} };
    }

    // ── Diff ──────────────────────────────────────────────────────────────
    case "diff": {
      return { action: "run_tool", toolName: "git_diff", toolArgs: { staged: false } };
    }

    // ── Review ────────────────────────────────────────────────────────────
    case "review": {
      return {
        action: "send",
        text: "Please review the staged git changes. Use git_diff with staged:true to see the diff, then provide a code review with: summary of changes, potential issues, suggestions, and approval/concerns.",
      };
    }

    // ── Commit message ────────────────────────────────────────────────────
    case "commit-message":
    case "commit_message": {
      return { action: "run_tool", toolName: "git_commit_message", toolArgs: {} };
    }

    // ── Commit (generate message + actually commit) ───────────────────────
    case "commit": {
      return { action: "commit" };
    }

    // ── PR description ────────────────────────────────────────────────────
    case "pr": {
      const base = args || "main";
      return {
        action: "send",
        text:
          `Generate a pull request description for changes relative to \`${base}\`.\n` +
          `1. Run: git log ${base}..HEAD --oneline\n` +
          `2. Run: git diff ${base}..HEAD --stat\n` +
          `3. Write a PR **title** (≤70 chars) and a description with these sections:\n` +
          `   ## Summary (2–4 bullet points)\n` +
          `   ## Changes (key files / what changed)\n` +
          `   ## Testing (how to verify)\n` +
          `Output only the PR text — no preamble.`,
      };
    }

    // ── Fix loop ──────────────────────────────────────────────────────────
    case "fix": {
      // /fix             → auto-detect test/build command
      // /fix cargo test  → use specified command
      return { action: "fix_loop", text: args || "" };
    }

    // ── Rollback ──────────────────────────────────────────────────────────
    case "rollback": {
      return { action: "rollback" };
    }

    // ── Copy last response ────────────────────────────────────────────────
    case "copy": {
      return { action: "copy_last" };
    }

    // ── Session history ───────────────────────────────────────────────────
    case "history": {
      return { action: "history" };
    }

    // ── Resume session ────────────────────────────────────────────────────
    case "resume": {
      if (!args) return { action: "history" }; // no index → show list
      return { action: "resume_session", text: args };
    }

    // ── Memory ────────────────────────────────────────────────────────────
    case "memory": {
      if (args === "save") {
        // Force an immediate memory update regardless of turn count
        return { action: "save_memory" };
      }
      if (!ctx.workspace.hasMemoryMd) {
        return { action: "print", text: "No .atlas/memory.md found. Run /init to create one.\nTip: memory is auto-saved every 3 turns, or use /memory save." };
      }
      return { action: "run_tool", toolName: "read_file", toolArgs: { path: ".atlas/memory.md" } };
    }

    // ── Plan ──────────────────────────────────────────────────────────────
    case "plan": {
      const goal = args || "the current task";
      return {
        action: "send",
        text: `Create a detailed step-by-step implementation plan for: ${goal}\n\nFor each step include: what to do, which files to change, potential risks, and how to verify it worked.`,
      };
    }

    // ── Security ──────────────────────────────────────────────────────────
    case "security": {
      return {
        action: "send",
        text: "Perform a security review of this codebase. Use list_files and grep to identify potential issues: hardcoded secrets, SQL injection, XSS, CSRF, insecure dependencies, overly permissive configs, missing auth checks, etc. Summarise findings by severity.",
      };
    }

    // ── Docs ──────────────────────────────────────────────────────────────
    case "docs": {
      const target = args || "the main module";
      return {
        action: "send",
        text: `Generate clear documentation for ${target}. Include: overview, function signatures with descriptions, parameters, return values, examples, and any important notes.`,
      };
    }

    // ── Undo ──────────────────────────────────────────────────────────────
    case "undo": {
      const recent = ctx.audit.getRecent(10);
      if (recent.length === 0) {
        return { action: "print", text: "No tool actions recorded in this session." };
      }
      const lines = ["**Recent tool actions (manual undo from .atlas/backups/):**"];
      for (const e of [...recent].reverse()) {
        const ts = new Date(e.timestamp).toLocaleTimeString();
        const argsStr = JSON.stringify(e.args).slice(0, 80);
        lines.push(`  [${ts}] ${e.tool}(${argsStr}) — ${e.success ? "ok" : "failed"}`);
      }
      lines.push("\nBackups are stored in .atlas/backups/");
      return { action: "print", text: lines.join("\n") };
    }

    // ── Exit ──────────────────────────────────────────────────────────────
    case "exit":
    case "quit":
    case "q":
      return { action: "exit" };

    // ── Custom commands ───────────────────────────────────────────────────
    default: {
      if (ctx.customCommands[name]) {
        const template = ctx.customCommands[name];
        const expanded = template.replace(/\$ARGUMENTS/g, args);
        return { action: "send", text: expanded };
      }
      return {
        action: "print",
        text: `Unknown command: /${name}\nType /help for available commands.`,
      };
    }
  }
}
