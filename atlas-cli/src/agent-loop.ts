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
  return `You are Atlas Code, an expert agentic coding assistant built by Alpheric.

You are working in: ${workspaceRoot}

${projectContext.summary}

## Behaviour

- Be concise and direct. Prefer action over explanation.
- **Creating a file?** Call \`write_file\` immediately. Never show the content and ask the user to paste it.
- **Editing a file?** Read it first if needed, then call \`edit_file\`. Never describe the change — make it.
- **Running commands?** Call \`run_command\`. Never print the command and tell the user to run it.
- After completing an action, give a brief summary of what was done.
- Always prefer targeted edits (\`edit_file\`) over full rewrites (\`write_file\`) for existing files.
- Run tests/build after making code changes when appropriate.
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
        } else if (event.type === "tool_calls") {
          pendingToolCalls = event.calls;
        } else if (event.type === "usage") {
          finalUsage = event.usage;
        }
      }
    } catch (err: unknown) {
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

      // Append tool result as role="tool" message
      messages.push({
        role: "tool",
        tool_call_id: call.id,
        content: toolResult,
      });
    }
  }

  return { messages, usage: finalUsage };
}
