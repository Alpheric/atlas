# Atlas Code CLI — Planning Document
**Owner:** Alpheric.AI | **Date:** 2026-04-30 | **Status:** Approved for v0 implementation
**Gateway baseline:** POST /v1/messages verified 35/35 tests passing (2026-04-30)

---

## 1. What Is Atlas Code?

Alpheric's branded agentic CLI. Routes all AI work through the Atlas Gateway
(https://atlas.alpheric.ai/v1/messages) instead of Anthropic's servers.
Users install one binary and get Claude Code feature parity with local-model routing,
Alpheric branding, and ATLAS.md project context — no Anthropic billing required.

---

## 2. Stack Decision: TypeScript + ink + Bun

| Concern | Choice | Reason |
|---|---|---|
| Language | TypeScript | Type-safe tool schemas, same ecosystem as Claude Code |
| TUI | ink | React-like components; best streaming UX in terminal |
| Runtime | Bun | Single-binary compile, 3x faster startup than Node |
| API client | @anthropic-ai/sdk | Already speaks /v1/messages wire protocol; zero custom code |
| Config | conf + keytar | JSON config in ~/.atlas/; OS keychain for API key |
| Markdown | marked + chalk | Code blocks, bold, headings in terminal |

---

## 3. Architecture

```
atlas-code/src/
  index.ts                CLI entrypoint — arg parse, command dispatch
  repl/
    App.tsx               Root ink component — chat history + input
    MessageBubble.tsx     Renders user/assistant/tool messages with markdown
    ToolCallBlock.tsx     Shows tool name, args, result inline
    PermissionPrompt.tsx  Permission gate (y/n/a)
    StatusBar.tsx         Model, tokens, cost, latency footer
  agent/
    loop.ts               Core agent loop: message -> tool -> message
    client.ts             Anthropic SDK configured to Atlas gateway
    context.ts            Project context loader (ATLAS.md, tree summary)
  tools/
    registry.ts           Tool definitions + handler map
    read_file.ts          read (never needs permission)
    write_file.ts         write (prompted if file exists)
    edit_file.ts          exact-match replace (hard error if not unique)
    bash.ts               exec (prompted; hardcoded denylist)
    glob.ts               read
    grep.ts               read (ripgrep if available)
    list_directory.ts     read
  permissions/
    gate.ts               Enforcement + logging
    policy.ts             default / auto-edit / yolo
    store.ts              Persisted approvals (~/.atlas/approvals.json)
  auth/
    login.ts              atlas login command
    keychain.ts           keytar wrapper with plaintext fallback
    config.ts             ~/.atlas/config.json read/write
  update/
    checker.ts            Background version check + upgrade prompt
```

### Agent Loop

```
user message
  -> inject system prompt + ATLAS.md context
  -> POST /v1/messages stream=true
       text_delta  -> render to REPL in real time
       stop_reason == "tool_use"
         -> for each tool_use block:
              permission gate -> execute -> result
         -> append assistant + tool_result turns
         -> loop (max --max-turns, default 20)
       stop_reason == "end_turn" -> done
```

### Tool Definitions

| Tool | Safety | Key constraint |
|---|---|---|
| read_file(path, start_line?, end_line?) | read | Binary files returned as [binary, N bytes] |
| write_file(path, content, create_dirs?) | write | Atomic write via tmp+rename |
| edit_file(path, old_str, new_str) | write | old_str must be unique; hard error otherwise |
| bash(command, timeout_ms?, working_dir?) | exec | Denylist: rm -rf /, fork bomb, sudo, curl|sh |
| glob(pattern, base_dir?) | read | fast-glob library |
| grep(pattern, path?, case_sensitive?, max_results?) | read | ripgrep if available |
| list_directory(path, recursive?, show_hidden?) | read | Tree format with file sizes |

### Permission Modes

| Mode | read | write | exec |
|---|---|---|---|
| default | auto | prompt per file | prompt per command |
| auto-edit | auto | auto | prompt per command |
| yolo | auto | auto | auto (denylist enforced) |

"Allow always" writes to ~/.atlas/approvals.json. Denylist blocks regardless of mode.

### Auth / Config

atlas login:
  1. Prompt for API key
  2. Test against GET /v1/models
  3. Store in OS keychain (keytar) or ~/.atlas/config.json fallback

~/.atlas/config.json:
  base_url, default_model, default_mode, max_turns, theme

Environment overrides: ATLAS_API_KEY, ATLAS_BASE_URL, ATLAS_MODEL

### API Client

  import Anthropic from "@anthropic-ai/sdk";
  const client = new Anthropic({ apiKey, baseURL: "https://atlas.alpheric.ai/v1" });

No custom HTTP client needed — the SDK speaks the exact wire protocol.

---

## 4. Phased Build Plan

### v0 — Chat REPL, no tools (~1 week, ~800 LOC)
Goal: atlas "hello" works end-to-end with streaming text.
Files: index.ts, repl/App.tsx, repl/MessageBubble.tsx, repl/StatusBar.tsx,
       agent/client.ts, agent/loop.ts (no tools), auth/login.ts, auth/config.ts
Tests: Vitest unit tests for config and client construction.
       Manual smoke: atlas "what is 2+2"

### v1 — Full agent loop + 7 tools + permissions (~4 weeks, ~3 000 LOC)
Goal: Multi-turn agentic tasks, Claude Code feature parity.
Added: all 7 tools, permission gate/policy/store, ToolCallBlock, PermissionPrompt,
       context.ts (ATLAS.md loader), update checker
Tests: Unit tests for all 7 tool handlers (mocked fs + child_process)
       Unit tests: permission gate — all 3 modes x 3 safety levels
       Integration: agent loop with mock Anthropic client, multi-turn tool_result
       E2E: atlas "create hello.txt saying hello" against live gateway

### v2 — MCP, polish, packaging (~4 weeks, ~2 000 LOC)
Goal: Production-ready, distributable, MCP support.
Added: MCP client (JSON-RPC, discovered tools join registry),
       session persistence (~/.atlas/sessions/),
       /compact command (summarise long context),
       atlas run <script.md> non-interactive mode,
       syntax highlighting (highlight.js),
       auto-update check
Tests: MCP adapter unit tests, session restore tests, E2E atlas run

---

## 5. Distribution

npm (primary — Node 18+):
  npm install -g @alpheric/atlas

Standalone binary (no Node required — via bun build --compile):
  Linux x64, macOS arm64, Windows x64
  Uploaded to GitHub Releases + https://install.alpheric.ai

curl installer:
  curl -fsSL https://install.alpheric.ai | sh
  (detects OS/arch, downloads binary, verifies SHA-256 checksum)

Auto-update:
  Background fetch to npm registry on each startup.
  Prints upgrade notice at session end. Silenced with ATLAS_NO_UPDATE_CHECK=1.

---

## 6. Open Questions (for Neeraj)

6.1 Vision / image support?
  Current: image blocks silently skipped in gateway.
  Recommendation: CLI blocks images with clear error for v1; route to vision provider in v2.

6.2 Thinking / extended reasoning?
  Current: anthropic-beta header ignored; thinking blocks stripped.
  Recommendation: keep stripping for v1 (Claude Code still works), surface in v2.

6.3 Context window limits?
  Atlas advertises 200K for all models but local 7B Ollama models have 8K-32K real context.
  Recommendation: add context-window-aware routing; downgrade gracefully before overflow.

6.4 Structured tool context passthrough?
  Currently tool_result blocks are flattened to plain text before CorePipeline.
  May reduce quality for multi-step code tasks. Evaluate for v2.

6.5 ATLAS.md vs CLAUDE.md?
  Recommendation: load both — ATLAS.md takes priority, CLAUDE.md is a fallback.
  All existing repos with CLAUDE.md work out of the box.

---

## 7. Gateway Changes Needed Before Atlas Code v1

| Gap | Impact | Fix |
|---|---|---|
| stop_sequences not forwarded to provider | Minor | Add stop field to CorePipelineInput, thread through _direct_provider_path |
| tool_use blocks not in streaming SSE | Tool calls invisible in streaming | Detect tool_calls in final chunk; emit tool_use content block before content_block_stop |
| Ping interval hard-coded (10s) | Fine for now | Make configurable |
| anthropic-beta header ignored | Thinking unavailable | Log and note; handle in v2 |

Most important before v1: tool_use in streaming SSE. Agentic tasks using stream=true
will not see tool calls in real time until this is fixed.

---

## 8. Verification Report

Date: 2026-04-30
Gateway: POST /v1/messages (src/a1/proxy/messages_router.py)
Test client: Anthropic Python SDK 0.97.0 + Claude Code CLI 2.1.119

Tests: 35/35 PASS (33 unit + 3 live against real Claude CLI)
Full suite: 105/105 PASS (zero regressions)

All acceptance criteria met:
  A.  Basic non-streaming: correct Anthropic MessagesResponse shape
  A'. Streaming SSE: all 7 event types in correct order (message_start,
      content_block_start, ping, content_block_delta, content_block_stop,
      message_delta, message_stop)
  B.  Tool definitions: Anthropic format parsed, forwarded to pipeline
  D.  tool_result content blocks: parsed and flattened correctly
  E.  Ping events: emitted at stream start + every 10s during generation
  F.  Auth: x-api-key and Authorization:Bearer both accepted
      Missing/bad key returns proper Anthropic error shape
      system string and block array both parsed
      tool_choice: auto/any/{type:tool,...} all handled
      stop_sequences: does not crash
      Model aliases: Atlas, atlas, atlas-plan, claude-* all route correctly
      Empty messages and malformed JSON return 400 with Anthropic error shape

---

## 9. Using Atlas with Claude Code

  # One session
  ANTHROPIC_BASE_URL=https://atlas.alpheric.ai/v1 \
  ANTHROPIC_AUTH_TOKEN=sk-atlas-FwcHfmI5qWzbohi2prMoixYBHAxEoxKEtN4qK2K9i38 \
  claude "create a fibonacci function in Python"

  # Permanent (add to ~/.bashrc or ~/.zshrc)
  export ANTHROPIC_BASE_URL=https://atlas.alpheric.ai/v1
  export ANTHROPIC_AUTH_TOKEN=sk-atlas-FwcHfmI5qWzbohi2prMoixYBHAxEoxKEtN4qK2K9i38

  # Model selection
  claude --model atlas-code "refactor this function"
  claude --model atlas-plan "design a new feature"
  claude --model Atlas "help me with this bug"    # auto-routed (default)
