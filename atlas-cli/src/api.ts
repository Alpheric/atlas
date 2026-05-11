/**
 * Atlas API client — uses /v1/chat/completions (OpenAI-compatible format).
 *
 * Tool calls are handled via native OpenAI function-calling format:
 *   - `tools` array is sent with the request
 *   - `tool_calls` in the response are parsed and returned to the agent loop
 *   - Results are sent back as role="tool" messages
 *
 * The backend routes tool requests to Vertex (Gemini) automatically,
 * avoiding MCP server interference in the claude-cli provider.
 */

import { AtlasConfig } from "./config.js";
import { OpenAITool } from "./tools/types.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ToolCallItem {
  id: string;
  type: "function";
  function: { name: string; arguments: string };
}

export interface Message {
  role: "user" | "assistant" | "system" | "tool";
  content: string | null;
  tool_calls?: ToolCallItem[];
  tool_call_id?: string;
}

export interface UsageInfo {
  inputTokens: number;
  outputTokens: number;
}

/** A resolved tool call with parsed args (not raw JSON string). */
export interface NativeToolCall {
  id: string;
  name: string;
  args: Record<string, unknown>;
}

/** Events emitted by streamCompletion. */
export type CompletionChunk =
  | { type: "text"; content: string }
  | { type: "tool_calls"; calls: NativeToolCall[] }
  | { type: "usage"; usage: UsageInfo };

// ---------------------------------------------------------------------------
// HTTP helpers
// ---------------------------------------------------------------------------

function buildHeaders(config: AtlasConfig): Record<string, string> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (config.apiKey) {
    headers["Authorization"] = `Bearer ${config.apiKey}`;
    headers["x-api-key"] = config.apiKey;
  }
  return headers;
}

function buildBody(
  config: AtlasConfig,
  messages: Message[],
  stream: boolean,
  tools?: OpenAITool[]
): string {
  const body: Record<string, unknown> = {
    model: config.model,
    max_tokens: 8192,
    stream,
    messages,
  };
  if (tools && tools.length > 0) {
    body.tools = tools;
    body.tool_choice = "auto";
  }
  return JSON.stringify(body);
}

// ---------------------------------------------------------------------------
// Accumulated tool call state during streaming
// ---------------------------------------------------------------------------

interface PartialToolCall {
  id: string;
  name: string;
  argumentsRaw: string;
}

// ---------------------------------------------------------------------------
// Streaming — yields CompletionChunk events
// ---------------------------------------------------------------------------

// 100-minute timeout — matches backend agent_execution_timeout
const FETCH_TIMEOUT_MS = 100 * 60 * 1000;

/** Stream a chat completion and yield CompletionChunk events. */
export async function* streamCompletion(
  config: AtlasConfig,
  messages: Message[],
  tools?: OpenAITool[],
  signal?: AbortSignal
): AsyncGenerator<CompletionChunk, void, unknown> {
  let response: Response;
  // Combine user abort signal with the global fetch timeout
  const timeoutSignal = AbortSignal.timeout(FETCH_TIMEOUT_MS);
  const combinedSignal = signal
    ? AbortSignal.any([signal, timeoutSignal])
    : timeoutSignal;
  try {
    response = await fetch(`${config.baseUrl}/chat/completions`, {
      method: "POST",
      headers: buildHeaders(config),
      body: buildBody(config, messages, true, tools),
      signal: combinedSignal,
    });
  } catch (err: unknown) {
    if (signal?.aborted) throw new DOMException("Interrupted by user", "AbortError");
    throw new Error(`Network error: ${err instanceof Error ? err.message : String(err)}`);
  }

  if (!response.ok) {
    const text = await response.text().catch(() => "");
    let detail = text;
    try {
      const j = JSON.parse(text);
      detail = j.error?.message ?? j.message ?? text;
    } catch { /* not JSON */ }
    throw new Error(`Atlas API ${response.status}: ${detail}`);
  }

  if (!response.body) throw new Error("No response body from Atlas API");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const partialCalls = new Map<number, PartialToolCall>();

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const data = line.slice(6).trim();
        if (data === "[DONE]") continue;

        let chunk: {
          choices?: Array<{
            delta?: {
              content?: string | null;
              tool_calls?: Array<{
                index?: number;
                id?: string;
                type?: string;
                function?: { name?: string; arguments?: string };
              }>;
            };
            finish_reason?: string | null;
          }>;
          usage?: { prompt_tokens?: number; completion_tokens?: number };
        };
        try {
          chunk = JSON.parse(data);
        } catch { continue; }

        const choice = chunk.choices?.[0];
        const delta = choice?.delta;

        // Text content
        if (delta?.content) {
          yield { type: "text", content: delta.content };
        }

        // Tool call deltas — accumulate
        if (delta?.tool_calls) {
          for (const tc of delta.tool_calls) {
            const idx = tc.index ?? 0;
            if (!partialCalls.has(idx)) {
              partialCalls.set(idx, { id: "", name: "", argumentsRaw: "" });
            }
            const partial = partialCalls.get(idx)!;
            if (tc.id) partial.id = tc.id;
            if (tc.function?.name) partial.name = tc.function.name;
            if (tc.function?.arguments) partial.argumentsRaw += tc.function.arguments;
          }
        }

        // finish_reason: tool_calls → emit all accumulated tool calls
        if (choice?.finish_reason === "tool_calls" && partialCalls.size > 0) {
          const calls: NativeToolCall[] = [];
          for (const [, partial] of [...partialCalls.entries()].sort(([a], [b]) => a - b)) {
            let args: Record<string, unknown> = {};
            try { args = JSON.parse(partial.argumentsRaw); } catch { /* malformed */ }
            calls.push({ id: partial.id || `call_${Date.now()}`, name: partial.name, args });
          }
          yield { type: "tool_calls", calls };
          partialCalls.clear();
        }

        // Usage info
        if (chunk.usage) {
          yield {
            type: "usage",
            usage: {
              inputTokens: chunk.usage.prompt_tokens ?? 0,
              outputTokens: chunk.usage.completion_tokens ?? 0,
            },
          };
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

// ---------------------------------------------------------------------------
// Non-streaming (for commit message generation etc.)
// ---------------------------------------------------------------------------

/** Collect a full completion (non-streaming). */
export async function completeSync(
  config: AtlasConfig,
  messages: Message[],
  signal?: AbortSignal
): Promise<{ text: string; usage?: UsageInfo }> {
  let response: Response;
  const timeoutSignal = AbortSignal.timeout(FETCH_TIMEOUT_MS);
  const combinedSignal = signal
    ? AbortSignal.any([signal, timeoutSignal])
    : timeoutSignal;
  try {
    response = await fetch(`${config.baseUrl}/chat/completions`, {
      method: "POST",
      headers: buildHeaders(config),
      body: buildBody(config, messages, false),
      signal: combinedSignal,
    });
  } catch (err: unknown) {
    if (signal?.aborted) throw new DOMException("Interrupted by user", "AbortError");
    throw new Error(`Network error: ${err instanceof Error ? err.message : String(err)}`);
  }

  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(`Atlas API ${response.status}: ${text}`);
  }

  const json = await response.json() as {
    choices?: Array<{ message?: { content?: string } }>;
    usage?: { prompt_tokens?: number; completion_tokens?: number };
  };

  const text = json.choices?.[0]?.message?.content ?? "";
  const usage: UsageInfo | undefined = json.usage
    ? { inputTokens: json.usage.prompt_tokens ?? 0, outputTokens: json.usage.completion_tokens ?? 0 }
    : undefined;

  return { text, usage };
}

// ---------------------------------------------------------------------------
// Backwards-compat helpers (still used by diff / commit message utilities)
// ---------------------------------------------------------------------------

/**
 * @deprecated Use streamCompletion instead.
 * Kept for legacy call sites that pass systemPrompt separately.
 */
export async function* streamMessage(
  config: AtlasConfig,
  messages: Array<{ role: "user" | "assistant"; content: string }>,
  systemPrompt?: string
): AsyncGenerator<string, UsageInfo | undefined, unknown> {
  const fullMessages: Message[] = [];
  if (systemPrompt) fullMessages.push({ role: "system", content: systemPrompt });
  fullMessages.push(...(messages as Message[]));

  let usage: UsageInfo | undefined;
  for await (const event of streamCompletion(config, fullMessages)) {
    if (event.type === "text") yield event.content;
    else if (event.type === "usage") usage = event.usage;
  }
  return usage;
}

// ---------------------------------------------------------------------------
// Legacy markdown tool-call parsing (kept for reference, no longer used)
// ---------------------------------------------------------------------------

/** @deprecated Native tool calling is now used instead of markdown blocks. */
export function parseToolCalls(_text: string) { return []; }

/** @deprecated Native tool calling is now used instead of markdown blocks. */
export function stripToolBlocks(text: string): string { return text; }
