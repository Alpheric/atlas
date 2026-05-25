/**
 * Agentic loop — drives multi-turn native tool calling.
 *
 * Flow:
 *   1. Build system prompt (workspace context + behaviour rules)
 *   2. Stream from the model with `tools` array (OpenAI function-calling format)
 *   3. For each tool_calls event:
 *        a. Safety check
 *        b. Permission check → allow | ask | deny
 *        c. Execute (if allowed) — diff preview for file writes
 *        d. Append assistant message (with tool_calls) + tool result messages
 *   4. Re-stream; repeat until no tool_calls (finish_reason: "stop")
 *
 * The backend routes requests that include `tools` to Vertex (Gemini) automatically,
 * bypassing claude-cli MCP server interference entirely.
 */

import { AtlasConfig } from "./config.js";
import { Message, streamCompletion, completeSync, UsageInfo, NativeToolCall, ToolCallItem } from "./api.js";
import { toolHandlers } from "./tools/implementations.js";
import { ALL_TOOL_DEFINITIONS, toOpenAITools, ToolCall } from "./tools/types.js";
import { PermissionConfig, checkPermission } from "./permissions.js";
import { checkCommand, isSecretFile, checkFileWrite } from "./safety.js";
import { AuditLog } from "./audit.js";
import { computeDiff, formatDiff } from "./diff.js";
import { autoRouteModel } from "./model-router.js";
import { ProjectContext } from "./project-context.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type AgentEventType =
  | "chunk"           // text chunk from model
  | "tool_call"       // model requested a tool
  | "tool_result"     // tool executed, result available
  | "approval_needed" // waiting for user approval
  | "approval_denied" // tool call denied
  | "diff"            // file diff preview
  | "error"           // error
  | "done";           // loop complete

export interface AgentEvent {
  type: AgentEventType;
  text?: string;
  toolCall?: ToolCall;
  toolResult?: string;
  diff?: string;
  error?: string;
  usage?: UsageInfo;
}

/** Callback invoked for each event during the agent loop. */
export type AgentEventHandler = (event: AgentEvent) => Promise<boolean | void>;
// For "approval_needed" events, return true to approve, false/undefined to deny.

// ---------------------------------------------------------------------------
// System prompt builder — no tool docs needed (passed via `tools` array)
// ---------------------------------------------------------------------------

export function buildSystemPrompt(
  projectContext: ProjectContext,
  workspaceRoot: string,
  customInstructions?: string
): string {
  return `You are Atlas Code, an expert agentic coding assistant built by Alpheric.AI.

You are working in: ${workspaceRoot}

${projectContext.summary}

## Identity (do not contradict)

- You are **Atlas, built by Alpheric.AI**. You are NOT Claude, GPT, Gemini,
  Llama, or any other model, and you were NOT built by Anthropic, OpenAI,
  Google, or Meta. Never claim otherwise.
- If asked your model, version, training data, or knowledge-cutoff date: say you
  are Atlas by Alpheric.AI and that the underlying model details and cutoff are
  not disclosed. Do NOT invent a vendor, version number, or date.

## Behaviour

- Be concise and direct. Prefer action over explanation.
- **Creating a file?** Call \`write_file\` immediately. Never show the content and ask the user to paste it.
- **Editing a file?** Read it first if needed, then call \`edit_file\`. Never describe the change — make it.
- **Running commands?** Call \`run_command\`. Never print the command and tell the user to run it.
- After completing an action, give a brief summary of what was done.
- Always prefer targeted edits (\`edit_file\`) over full rewrites (\`write_file\`) for existing files.
- Run tests/build after making code changes when appropriate.

## Following instructions (do not deviate)

- Follow the user's explicit instructions exactly. If they say "use inline CSS",
  use inline CSS — do not substitute your own preferred approach. If you believe
  a different approach is better, do it their way first, then note the suggestion.
- If you say you will do something ("applying the fix now"), actually call the
  tool in the same turn. Never confirm an action and then refuse or stall.

## Accuracy (never fabricate)

- Do not state facts you are unsure of — especially product names, model names,
  versions, or APIs. If you don't know, say so or use \`web_search\` to verify.
- Never invent libraries, flags, or model names (e.g. do not assert a "GPT-5"
  variant exists without verifying). When asked about something you don't
  recognise, search before answering rather than refusing.

## Avoid loops and wasted work

- Track what you have already done this session. Do not re-create directories or
  files you already created, and do not re-run a command that already succeeded.
- If the same tool call fails or returns the same result twice, change strategy
  — do not repeat it. After two failed attempts at the same thing, stop and
  report what is blocking you instead of retrying identically.
- Confirm the working directory before assuming it; if it's ambiguous, ask.

## Handle failures honestly

- When a command fails (non-zero exit) — e.g. \`pip install\` or \`npm install\`
  errors — say so explicitly and either fix the root cause or stop. Never
  silently ignore a failure.
- Never substitute a different deliverable than what was asked because a
  dependency failed. If you were asked for a .docx and the library won't
  install, report that — don't quietly produce a .md instead.

## Multi-step projects

- For "create a project" tasks, after making a directory move on immediately to
  scaffolding files and installing dependencies. Do not call mkdir/create_directory
  repeatedly — create the folder once, then build inside it.
${customInstructions ? `\n## Project Instructions\n\n${customInstructions}` : ""}`;
}

// ---------------------------------------------------------------------------
// Safety + permission gate
// ---------------------------------------------------------------------------

interface GateResult {
  decision: "allow" | "deny" | "ask";
  reason?: string;
}

function safetyGate(
  call: NativeToolCall,
  workspaceRoot: string,
  permissions: PermissionConfig
): GateResult {
  // 1. Hard deny — secret files
  if (call.name === "read_file" || call.name === "write_file" || call.name === "edit_file") {
    const filePath = String(call.args.path ?? "");
    if (isSecretFile(filePath)) {
      return { decision: "deny", reason: `Secret file blocked: ${filePath}` };
    }
  }

  // 2. Hard deny — workspace escape for writes
  if (call.name === "write_file" || call.name === "edit_file") {
    const filePath = String(call.args.path ?? "");
    const check = checkFileWrite(filePath, workspaceRoot);
    if (!check.safe) {
      return { decision: "deny", reason: check.reason };
    }
  }

  // 3. Dangerous command check
  if (call.name === "run_command") {
    const cmd = String(call.args.command ?? "");
    const check = checkCommand(cmd);
    if (!check.safe) {
      return { decision: "ask", reason: `⚠️  ${check.reason}` };
    }
  }

  // 4. Permission config check
  const perm = checkPermission(call.name, permissions);
  return { decision: perm };
}

// ---------------------------------------------------------------------------
// Main agent loop
// ---------------------------------------------------------------------------

export interface AgentLoopOptions {
  config: AtlasConfig;
  messages: Message[];
  systemPrompt: string;
  permissions: PermissionConfig;
  workspaceRoot: string;
  audit: AuditLog;
  autoRoute?: boolean;
  onEvent: AgentEventHandler;
  maxTurns?: number;
  signal?: AbortSignal;  // Esc-to-interrupt
  conversationId?: string;  // threaded to backend so multi-turn chats stay in one DB row
}

// Pre-build the OpenAI tools array once — it never changes
const OPENAI_TOOLS = toOpenAITools(ALL_TOOL_DEFINITIONS);

export async function runAgentLoop(opts: AgentLoopOptions): Promise<{
  messages: Message[];
  usage?: UsageInfo;
}> {
  const {
    permissions,
    workspaceRoot,
    audit,
    onEvent,
    maxTurns = 15,
    signal,
  } = opts;

  let messages = [...opts.messages];
  let finalUsage: UsageInfo | undefined;
  let config = { ...opts.config };

  // Loop / no-progress detection. Three independent signals:
  //  1. identical call (name+args) repeated  → blocked before executing
  //  2. same (name+result) repeated          → no-progress, abort after
  //  3. global tool-call budget exceeded      → backstop for varying-arg thrash
  // Crucially the correction is written into the message history (a tool
  // message the model actually reads), not just printed to the user — the
  // model ignored UI-only warnings in earlier reports.
  const toolCallCounts = new Map<string, number>(); // name+args
  const resultCounts = new Map<string, number>();    // name+result (no-progress)
  let totalToolCalls = 0;
  const SIG_REPEAT_LIMIT = 3;       // identical args N× → block + abort
  const NO_PROGRESS_LIMIT = 3;      // same tool+result N× → abort
  const GLOBAL_CALL_BUDGET = 100;   // total calls per run → backstop
  let loopAborted = false;

  // Auto-route model if enabled
  if (opts.autoRoute) {
    const lastUser = [...messages].reverse().find((m) => m.role === "user");
    if (lastUser) {
      const route = autoRouteModel(lastUser.content ?? "", config.model);
      if (route.model !== config.model) {
        config = { ...config, model: route.model };
        await onEvent({ type: "chunk", text: `_[auto-routed to ${route.model}: ${route.reason}]_\n\n` });
      }
    }
  }

  for (let turn = 0; turn < maxTurns; turn++) {
    // Check abort before each turn
    if (signal?.aborted) {
      await onEvent({ type: "done", usage: finalUsage });
      break;
    }

    let pendingToolCalls: NativeToolCall[] = [];
    let assistantText = "";

    // BUG-11: thinking-loop guard. Detect when the model streams the same
    // non-trivial line over and over (reasoning that never terminates) and
    // abort the stream instead of letting it run forever.
    const lineRepeat = new Map<string, number>();
    let pendingLine = "";
    let thinkingLoop = false;
    const LINE_REPEAT_LIMIT = 12;

    // ── Stream model response ──────────────────────────────────────────────
    try {
      const fullMessages: Message[] = [
        { role: "system", content: opts.systemPrompt },
        ...messages,
      ];

      for await (const event of streamCompletion(config, fullMessages, OPENAI_TOOLS, signal, opts.conversationId)) {
        if (event.type === "text") {
          assistantText += event.content;
          await onEvent({ type: "chunk", text: event.content });

          // Repetition detection on completed lines.
          pendingLine += event.content;
          let nl: number;
          while ((nl = pendingLine.indexOf("\n")) >= 0) {
            const line = pendingLine.slice(0, nl).trim();
            pendingLine = pendingLine.slice(nl + 1);
            if (line.length >= 25) {
              const c = (lineRepeat.get(line) ?? 0) + 1;
              lineRepeat.set(line, c);
              if (c > LINE_REPEAT_LIMIT) {
                thinkingLoop = true;
                break;
              }
            }
          }
          if (thinkingLoop) break; // stop consuming the stream
        } else if (event.type === "tool_calls") {
          pendingToolCalls = event.calls;
        } else if (event.type === "usage") {
          finalUsage = event.usage;
        }
      }

      if (thinkingLoop) {
        if (assistantText.trim()) {
          messages.push({ role: "assistant", content: assistantText });
        }
        await onEvent({
          type: "error",
          error: "Stopped: the model was repeating the same reasoning without completing.",
        });
        break;
      }
    } catch (err: unknown) {
      // BUG-01: preserve partial assistant text on timeout/abort so work isn't
      // silently lost. Push whatever streamed before the failure.
      if (assistantText.trim()) {
        messages.push({ role: "assistant", content: assistantText });
      }
      if (signal?.aborted || (err instanceof DOMException && err.name === "AbortError")) {
        await onEvent({ type: "done", usage: finalUsage });
        break;
      }
      await onEvent({ type: "error", error: err instanceof Error ? err.message : String(err) });
      break;
    }

    // ── No tool calls → done ───────────────────────────────────────────────
    if (pendingToolCalls.length === 0) {
      await onEvent({ type: "done", usage: finalUsage });
      break;
    }

    // ── Build assistant message with tool_calls array ──────────────────────
    const toolCallItems: ToolCallItem[] = pendingToolCalls.map((tc) => ({
      id: tc.id,
      type: "function",
      function: {
        name: tc.name,
        arguments: JSON.stringify(tc.args),
      },
    }));

    messages.push({
      role: "assistant",
      content: assistantText || null,
      tool_calls: toolCallItems,
    });

    // ── Execute each tool call, append tool result messages ────────────────
    // (OpenAI requires one tool result message per tool call)
    for (const call of pendingToolCalls) {
      // ── Loop / no-progress guard ──────────────────────────────────────────
      const sig = `${call.name}:${JSON.stringify(call.args)}`;
      const seen = (toolCallCounts.get(sig) ?? 0) + 1;
      toolCallCounts.set(sig, seen);
      totalToolCalls += 1;

      // (3) Global backstop — catches varying-arg thrashing (e.g. mkdir of
      // hundreds of slightly different paths) that per-signature checks miss.
      if (totalToolCalls > GLOBAL_CALL_BUDGET) {
        messages.push({
          role: "tool",
          tool_call_id: call.id,
          content:
            `[Stopped] You have made over ${GLOBAL_CALL_BUDGET} tool calls without ` +
            `completing the task. Stop now and report what you accomplished and what is blocking you.`,
        });
        loopAborted = true;
        continue;
      }

      // (1) Identical call repeated → block BEFORE executing it again.
      if (seen >= SIG_REPEAT_LIMIT) {
        await onEvent({
          type: "tool_result",
          toolResult: `[Loop blocked: '${call.name}' called ${seen}× with identical arguments.]`,
        });
        messages.push({
          role: "tool",
          tool_call_id: call.id,
          content:
            `[Loop blocked] You called ${call.name} with identical arguments ${seen} times — ` +
            `it is NOT making progress. Do something different (read a file, run a different ` +
            `command, write code) or stop and report what is blocking you. Do not repeat this call.`,
        });
        loopAborted = true;
        continue;
      }
      if (seen === SIG_REPEAT_LIMIT - 1) {
        await onEvent({
          type: "chunk",
          text: `\n_[warning: '${call.name}' repeated — change approach or it will be stopped]_\n`,
        });
      }

      await onEvent({ type: "tool_call", toolCall: { name: call.name, args: call.args } });

      const gate = safetyGate(call, workspaceRoot, permissions);

      if (gate.decision === "deny") {
        const reason = gate.reason ?? "Denied by permission policy";
        await onEvent({ type: "approval_denied", text: reason });
        messages.push({
          role: "tool",
          tool_call_id: call.id,
          content: `[Denied: ${reason}]`,
        });
        continue;
      }

      if (gate.decision === "ask") {
        const approved = await onEvent({
          type: "approval_needed",
          toolCall: { name: call.name, args: call.args },
          text: gate.reason,
        });

        if (!approved) {
          await onEvent({ type: "approval_denied", text: "User denied the tool call." });
          messages.push({
            role: "tool",
            tool_call_id: call.id,
            content: `[Denied by user]`,
          });
          continue;
        }
      }

      // ── Diff preview for file writes/edits ────────────────────────────────
      if (call.name === "write_file" || call.name === "edit_file") {
        try {
          const { existsSync, readFileSync } = await import("fs");
          const { resolve } = await import("path");
          const filePath = String(call.args.path ?? "");
          const absPath = resolve(workspaceRoot, filePath);

          if (existsSync(absPath)) {
            const oldContent = readFileSync(absPath, "utf-8");
            let newContent: string;

            if (call.name === "write_file") {
              newContent = String(call.args.content ?? "");
            } else {
              const oldStr = String(call.args.old_string ?? "");
              const newStr = String(call.args.new_string ?? "");
              const idx = oldContent.indexOf(oldStr);
              newContent = idx >= 0
                ? oldContent.slice(0, idx) + newStr + oldContent.slice(idx + oldStr.length)
                : oldContent;
            }

            const diff = computeDiff(oldContent, newContent, filePath);
            const formatted = formatDiff(diff);
            if (formatted.trim()) {
              await onEvent({ type: "diff", diff: formatted });
            }
          }
        } catch {
          // Diff preview failed — proceed anyway
        }
      }

      // ── Execute tool ───────────────────────────────────────────────────────
      const handler = toolHandlers[call.name];
      const t0 = Date.now();

      let toolResult: string;
      let success = false;

      if (!handler) {
        toolResult = `Unknown tool: ${call.name}`;
      } else {
        try {
          const res = await handler(call.args, workspaceRoot);
          success = res.success;
          toolResult = res.success ? res.output : `Error: ${res.error ?? "unknown error"}`;
        } catch (err: unknown) {
          toolResult = `Tool threw: ${err instanceof Error ? err.message : String(err)}`;
        }
      }

      // Audit
      audit.record({
        timestamp: new Date().toISOString(),
        tool: call.name,
        args: call.args,
        success,
        error: success ? undefined : toolResult,
        durationMs: Date.now() - t0,
      });

      await onEvent({ type: "tool_result", toolResult });

      // BUG-04: when a tool fails, append an explicit recovery directive so the
      // model diagnoses and tries a *different* approach instead of giving up
      // or silently retrying the same thing. The loop guard above prevents
      // identical retries; this nudges toward an actual fix.
      let toolMessage = toolResult;
      if (!success) {
        const tries = toolCallCounts.get(sig) ?? 1;
        if (tries === 1) {
          toolMessage +=
            "\n\n[Recovery] This call FAILED (non-zero exit). Tell the user it failed, " +
            "diagnose the cause from the error above, and take a corrective action (read " +
            "the relevant file/logs, fix the root cause, or adjust the command). Do NOT " +
            "silently continue or switch to a different deliverable than the user asked for.";
        } else {
          toolMessage +=
            `\n\n[Recovery] This has now failed ${tries} times. Stop retrying this ` +
            "approach. Either try a materially different strategy or stop and report " +
            "exactly what is blocking you.";
        }
      }

      // (2) No-progress detection: same tool returning the same result repeatedly
      // (e.g. `ls` showing the same tree, `npm install` re-succeeding with the
      // same output, `mkdir` of an existing dir) means the task isn't advancing.
      const resultSig = `${call.name}::${(toolResult || "").slice(0, 400)}`;
      const rseen = (resultCounts.get(resultSig) ?? 0) + 1;
      resultCounts.set(resultSig, rseen);
      if (rseen >= NO_PROGRESS_LIMIT) {
        toolMessage +=
          `\n\n[No progress] '${call.name}' has returned the same result ${rseen} times. ` +
          `This is not advancing the task. Stop repeating it — take a different action ` +
          `(read/write files, run a different command) or stop and report what is blocking you.`;
        loopAborted = true;
      }

      // Append tool result as role="tool" message
      messages.push({
        role: "tool",
        tool_call_id: call.id,
        content: toolMessage,
      });
    }

    // BUG-10: a hard loop was detected this turn — stop the agent loop so it
    // can't keep hammering the same call across turns.
    if (loopAborted) {
      await onEvent({
        type: "error",
        error: "Stopped: the agent was repeating the same action without making progress.",
      });
      break;
    }
  }

  return { messages, usage: finalUsage };
}
