"""Claude CLI proxy provider — routes requests through the local Claude CLI.

Uses the `claude` command-line tool (which handles its own OAuth/auth)
to forward completion requests to Anthropic's API. This avoids needing
a separate API key since the CLI manages token refresh automatically.
"""

import asyncio
import json
import re
import uuid
from collections.abc import AsyncIterator

from a1.common.logging import get_logger
from a1.common.tokens import count_messages_tokens_for_model, count_tokens_for_model
from a1.providers.base import LLMProvider, ModelInfo
from a1.proxy.request_models import ChatCompletionRequest
from a1.proxy.response_models import (
    ChatCompletionChunk,
    ChatCompletionResponse,
    Choice,
    ChoiceMessage,
    DeltaMessage,
    StreamChoice,
    Usage,
)

log = get_logger("providers.claude_cli")

# Cache of atlas-model-name → system_prompt_suffix loaded from providers.yaml
_atlas_suffixes: dict[str, str] | None = None


def get_atlas_system_suffix(atlas_model: str) -> str:
    """Return the domain-specific system prompt suffix for an Atlas model.

    Loads config/providers.yaml on first call and caches the result.
    Returns empty string if model not found or YAML unavailable.
    """
    global _atlas_suffixes
    if _atlas_suffixes is None:
        _atlas_suffixes = {}
        try:
            import os

            import yaml

            config_path = os.path.join(
                os.path.dirname(__file__), "..", "..", "..", "config", "providers.yaml"
            )
            config_path = os.path.normpath(config_path)
            with open(config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            for model_cfg in data.get("providers", {}).get("atlas", {}).get("models", []):
                name = model_cfg.get("name", "")
                suffix = model_cfg.get("system_prompt_suffix", "")
                if name and suffix:
                    _atlas_suffixes[name] = suffix.strip()
        except Exception as e:
            log.warning(f"Could not load atlas suffixes from providers.yaml: {e}")
    return _atlas_suffixes.get(atlas_model, "")


async def get_atlas_system_suffix_async(atlas_model: str) -> str:
    """Registry-aware suffix lookup. An active prompt version named
    ``atlas_suffix:<model>`` overrides the providers.yaml value, so the 7 Atlas
    system-prompt suffixes become editable/versionable via the Prompts UI
    without a redeploy. Falls back to the YAML suffix (this function's sync
    counterpart) when no override exists."""
    yaml_default = get_atlas_system_suffix(atlas_model)
    try:
        from a1.common.prompt_registry import get_prompt

        return await get_prompt(f"atlas_suffix:{atlas_model}", default=yaml_default)
    except Exception:
        return yaml_default


# Models available through Claude CLI (Max subscription)
CLAUDE_CLI_MODELS = [
    ModelInfo(
        name="claude-sonnet-4-20250514",
        provider="claude-cli",
        context_window=200000,
        cost_per_1k_input=0.003,
        cost_per_1k_output=0.015,
        supports_tools=True,
        supports_streaming=True,
    ),
    ModelInfo(
        name="claude-haiku-4-5-20251001",
        provider="claude-cli",
        context_window=200000,
        cost_per_1k_input=0.001,
        cost_per_1k_output=0.005,
        supports_tools=True,
        supports_streaming=True,
    ),
    ModelInfo(
        name="claude-opus-4-20250514",
        provider="claude-cli",
        context_window=200000,
        cost_per_1k_input=0.015,
        cost_per_1k_output=0.075,
        supports_tools=True,
        supports_streaming=True,
    ),
]


class ClaudeCLIProvider(LLMProvider):
    """Provider that proxies requests through the local Claude CLI.

    The CLI handles authentication (OAuth token refresh) automatically,
    so we just pipe prompts through it and parse the output.
    """

    name = "claude-cli"

    def __init__(self):
        self._healthy = False
        self._cli_path = self._find_claude_cli()
        self._models = list(CLAUDE_CLI_MODELS)
        log.debug(f"Claude CLI path: {self._cli_path}")

    def _build_cmd(self, args: list[str]) -> list[str]:
        """Return full command list for the CLI. Subclasses can prepend sudo etc."""
        return [self._cli_path] + args

    def _effective_home(self) -> str | None:
        """Return HOME override for subprocess env, or None to use process default.

        Overridden by ClaudeCLIAccount to use a minimal HOME directory that
        contains only credentials — no plugins or MCP server configs.
        """
        return None

    @staticmethod
    def _find_claude_cli(home_dir: str | None = None) -> str:
        """Find the claude CLI executable path.

        Checks (in order):
        1. CLAUDE_CODE_EXECPATH env var (set by Claude Code itself)
        2. ~/.claude/remote/ccd-cli/<version> glob (Claude Code install layout)
        3. Common PATH locations
        """
        import glob
        import os
        import shutil
        import sys

        effective_home = home_dir or os.path.expanduser("~")
        current_home = os.path.expanduser("~")

        # 1. Env var set by Claude Code harness — only trust it for the current user.
        #    When home_dir points to a different user, skip this to avoid returning
        #    the wrong user's binary.
        if home_dir is None or os.path.abspath(home_dir) == os.path.abspath(current_home):
            exec_path = os.environ.get("CLAUDE_CODE_EXECPATH")
            if exec_path and os.path.isfile(exec_path) and os.access(exec_path, os.X_OK):
                return exec_path

        # 2. Versioned binary in ~/.claude/remote/ccd-cli/ (Claude Code layout)
        ccd_pattern = os.path.join(effective_home, ".claude", "remote", "ccd-cli", "*")
        ccd_matches = sorted(glob.glob(ccd_pattern), reverse=True)  # newest version first
        for match in ccd_matches:
            if os.path.isfile(match) and os.access(match, os.X_OK):
                return match

        # 3. Common PATH / Windows locations.
        #    For a different user's home, skip shutil.which — it reflects the *current*
        #    user's PATH and would return a path like /home/neeraj/.local/bin/claude
        #    that sudo cannot execute on behalf of the other user.  Instead check:
        #      a) the target user's own ~/.local/bin/claude
        #      b) well-known system-wide locations
        is_other_user = home_dir and os.path.abspath(home_dir) != os.path.abspath(current_home)

        if is_other_user:
            candidates = [
                os.path.join(effective_home, ".local", "bin", "claude"),
                "/usr/local/bin/claude",
                "/usr/bin/claude",
                os.path.join(effective_home, "AppData", "Roaming", "npm", "claude.cmd"),
                os.path.join(effective_home, "AppData", "Roaming", "npm", "claude"),
            ]
        else:
            candidates = [
                shutil.which("claude"),
                shutil.which("claude.cmd"),
                os.path.join(effective_home, "AppData", "Roaming", "npm", "claude.cmd"),
                os.path.join(effective_home, "AppData", "Roaming", "npm", "claude"),
                "/usr/local/bin/claude",
            ]
        for path in candidates:
            if path and os.path.exists(path):
                return path

        # 4. For other-user accounts: fall back to system-wide then current user's binary.
        if is_other_user:
            exec_path = os.environ.get("CLAUDE_CODE_EXECPATH")
            if exec_path and os.path.isfile(exec_path) and os.access(exec_path, os.X_OK):
                return exec_path
            # Try current user's ccd-cli dir
            ccd_pattern_current = os.path.join(current_home, ".claude", "remote", "ccd-cli", "*")
            for match in sorted(glob.glob(ccd_pattern_current), reverse=True):
                if os.path.isfile(match) and os.access(match, os.X_OK):
                    return match

        # Final fallback — let the OS find it via shell
        return "claude.cmd" if sys.platform == "win32" else "claude"

    @staticmethod
    def _strip_tool_definitions(text: str) -> str:
        """Remove tool definition blocks from system prompt.

        Claude CLI can't execute tools, so including them
        causes Claude to attempt tool_use and waste turns.
        """
        import re

        # Remove "Available tools:" block (bullet list)
        text = re.sub(
            r"\n*Available tools:\n(?:- .+\n?)+",
            "",
            text,
        )
        # Remove "## Tooling" section entirely
        text = re.sub(
            r"\n*## Tooling\n[\s\S]*?(?=\n## |\Z)",
            "",
            text,
        )
        # Remove "## Tools" section entirely
        text = re.sub(
            r"\n*## Tools\n[\s\S]*?(?=\n## |\Z)",
            "",
            text,
        )
        # Remove "Tool availability" lines
        text = re.sub(
            r"Tool availability[^\n]*\n?",
            "",
            text,
        )
        text = re.sub(
            r"Tool names are case-sensitive[^\n]*\n?",
            "",
            text,
        )
        # Remove lines like "- toolname(params): desc"
        text = re.sub(
            r"\n- \w+\([^)]*\):[^\n]+",
            "",
            text,
        )
        return text.strip()

    async def _run_claude(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 1000,
        skip_tool_strip: bool = False,
    ) -> dict:
        """Run the claude CLI with a prompt and return parsed JSON result.

        Returns dict with: text, input_tokens, output_tokens, cache_read_tokens,
        cost_usd, api_duration_ms
        skip_tool_strip: set True when we've already injected tool defs ourselves — avoids
        double-stripping the carefully crafted tool instructions.
        """
        if not skip_tool_strip:
            # Strip stray tool definition blocks from system prompts that weren't
            # prepared by us (e.g. raw forwarded system prompts from the client).
            system = self._strip_tool_definitions(system)

        # --tools "" disables ALL built-in tools
        # (correct flag; --allowedTools is an allowlist, not a blocklist)
        # --bare skips CLAUDE.md auto-discovery, auto-memory, hooks, LSP
        # --no-session-persistence stops Claude from saving sessions
        #
        # --max-turns 1: single-turn only.  We previously used 2 turns when
        # skip_tool_strip=True but that caused Claude to emit "I don't have
        # terminal access" on turn 2 after native tool_use failed on turn 1.
        # The error_max_turns recovery below handles the turn-1 native block.
        max_turns = "1"
        args = [
            "-p",
            prompt,
            "--max-turns",
            max_turns,
            "--output-format",
            "json",
            "--tools",
            "",  # disable all built-in tools so Claude responds with text only
            "--no-session-persistence",  # don't save/load sessions from disk
            "--mcp-config", '{"mcpServers":{}}',  # clear local MCP server config
            "--disallowedTools",          # block account-level remote MCP tools
            "mcp__claude_ai_Google_Drive__authenticate,mcp__claude_ai_Google_Drive__complete_authentication",
        ]

        # Build the --system-prompt value.
        # When tool defs are injected (skip_tool_strip=True) the system prompt already
        # contains the tool calling instructions — merge Atlas identity with them.
        # When no tools, add the "text only" guard to prevent spurious tool attempts.
        if skip_tool_strip and system:
            # Tools mode: identity + full system prompt (which includes tool defs + instructions)
            atlas_identity = (
                "You are Atlas, an AI assistant by Alpheric.AI. "
                "Never identify as Claude, Anthropic, or any other AI. "
                "You are Atlas and your responses represent the Alpheric.AI platform.\n\n" + system
            )
            effective_system = ""  # already folded into atlas_identity above
        else:
            atlas_identity = (
                "You are Atlas, an AI assistant by Alpheric.AI. "
                "Never identify as Claude, Anthropic, or any other AI. "
                "You are Atlas and your responses represent the Alpheric.AI platform. "
                "Respond with text only. Do not use any tools."
            )
            effective_system = system

        # Always use stdin to pipe the prompt — avoids Windows cmd.exe quoting issues
        # with special characters (Unicode arrows, brackets, newlines) in the prompt.
        stdin_text = (
            f"[System Instructions]\n{effective_system}\n\n[User Message]\n{prompt}"
            if effective_system
            else prompt
        )
        stdin_data = stdin_text.encode("utf-8")
        args[1] = "-"  # replace prompt arg with "-" (read from stdin)
        args.extend(["--system-prompt", atlas_identity])

        output, code, stderr = await self._exec(
            args,
            timeout=120,
            stdin_data=stdin_data,
        )

        if code != 0:
            # Give operators enough context to diagnose silent failures.
            # The CLI sometimes exits non-zero with empty stderr (e.g. when an
            # MCP config rejection happens early) — in those cases stdout is
            # the only signal we have. Always log both, plus the account
            # context (which CLI binary, which unix user) so we can correlate
            # with the right ~/.claude credentials.
            unix_user = getattr(self, "unix_user", None)
            account = f" account={unix_user}" if unix_user else ""
            stderr_snip = stderr[:500] if stderr else "empty"
            stdout_snip = output[:500] if output else "empty"
            log.error(
                f"Claude CLI exit={code}{account} cli={self._cli_path} "
                f"stderr={stderr_snip} stdout={stdout_snip}"
            )
            if not output:
                # No stdout at all — surface stderr (or note it was empty too).
                detail = stderr[:300] if stderr else "no output on stdout or stderr"
                raise RuntimeError(f"Claude CLI exit code {code}: {detail}")

        # Parse JSON response for accurate token counts
        try:
            import json

            data = json.loads(output)

            # Extract text — handle error_max_turns where
            # result may be empty because Claude tried native tool_use
            text = data.get("result", "")
            if not text and data.get("subtype") == "error_max_turns":
                # Try to recover tool_use blocks from the messages array.
                # When Claude uses native tool_use (which fails because --tools "")
                # the CLI stores the assistant's content blocks in messages[].
                # Convert any tool_use blocks back to our <tool_call> XML format
                # so downstream parsing can extract them properly.
                recovered_xml = []
                for msg in data.get("messages", []):
                    if msg.get("role") == "assistant":
                        for block in msg.get("content", []):
                            if isinstance(block, dict) and block.get("type") == "tool_use":
                                tc_json = json.dumps(
                                    {
                                        "name": block.get("name", "unknown"),
                                        "input": block.get("input", {}),
                                    }
                                )
                                recovered_xml.append(f"<tool_call>{tc_json}</tool_call>")
                if recovered_xml:
                    text = "\n".join(recovered_xml)
                    log.info(
                        f"[tool_use] Recovered {len(recovered_xml)} tool_use block(s) "
                        "from error_max_turns response"
                    )
                else:
                    log.warning("Claude hit max turns (tool_use) — no tool blocks recoverable")
                    text = (
                        "I can help with that, but I don't have "
                        "access to external tools. Let me answer "
                        "based on my knowledge instead."
                    )

            return {
                "text": text or output,
                "input_tokens": data.get(
                    "usage",
                    {},
                ).get("input_tokens", 0),
                "output_tokens": data.get(
                    "usage",
                    {},
                ).get("output_tokens", 0),
                "cache_read_tokens": data.get(
                    "usage",
                    {},
                ).get("cache_read_input_tokens", 0),
                "cost_usd": data.get("total_cost_usd", 0.0),
                "api_duration_ms": data.get(
                    "duration_api_ms",
                    0,
                ),
            }
        except (json.JSONDecodeError, KeyError):
            return {
                "text": output,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cost_usd": 0.0,
                "api_duration_ms": 0,
            }

    # ------------------------------------------------------------------
    # Tool-use helpers (prompt-engineering approach — no API key needed)
    # ------------------------------------------------------------------

    @staticmethod
    def _inject_tools_system(system: str, tools: list) -> str:
        """Append tool definitions and calling instructions to the system prompt.

        Uses an XML <tool_call> convention so Claude outputs structured JSON
        that we can parse back into proper tool_use blocks for the client.
        The client (e.g. Hermes/Ares) executes tools locally and sends
        tool_result content back in the next turn.

        IMPORTANT: We explicitly forbid native Anthropic tool_use API blocks
        because the CLI cannot execute user-defined tools. Claude MUST output
        the plain-text <tool_call> XML format so our parser can extract it.
        """
        schemas = []
        for t in tools:
            fn = t.function
            schemas.append(
                {
                    "name": fn.name,
                    "description": fn.description or "",
                    "parameters": fn.parameters or {"type": "object", "properties": {}},
                }
            )

        tool_json = json.dumps(schemas, indent=2)
        instructions = (
            "\n\n## TOOL CALLING — READ CAREFULLY\n"
            "CRITICAL: You are running in a text-only mode. The Anthropic native tool_use "
            "API is NOT available and will cause an error. You MUST use the plain-text "
            "format below — no exceptions.\n\n"
            "When you need to call a tool, output THIS EXACT FORMAT on a single line "
            "(and nothing else on that line):\n\n"
            '<tool_call>{"name": "TOOL_NAME", "input": {"param": "value"}}</tool_call>\n\n'
            "Rules:\n"
            "1. NEVER use native tool_use API calls — always use the <tool_call> text format.\n"
            "2. Only one <tool_call> per response.\n"
            "3. No extra text on the same line as <tool_call>.\n"
            "4. If no tool is needed, respond normally with plain text.\n"
            "5. After receiving a tool result, continue reasoning and give the final answer.\n\n"
            f"Available tools (JSON schema):\n```json\n{tool_json}\n```\n\n"
            "Example — to call the 'terminal' tool:\n"
            '<tool_call>{"name": "terminal", "input": {"command": "ls -la"}}</tool_call>'
        )
        return (system.rstrip() + instructions) if system else instructions.strip()

    @staticmethod
    def _parse_tool_calls(text: str) -> tuple[str, list[dict] | None]:
        """Extract <tool_call> blocks from Claude's text response.

        Handles single-line and multi-line <tool_call>…</tool_call> blocks.
        Returns (clean_text_without_tool_calls, tool_calls_list_or_None).
        """
        tool_calls: list[dict] = []

        tc_re = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)

        def _replace(m: re.Match) -> str:
            raw = m.group(1).strip()
            try:
                data = json.loads(raw)
                tool_calls.append(
                    {
                        "id": f"toolu_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {
                            "name": data.get("name", "unknown"),
                            "arguments": json.dumps(data.get("input", {})),
                        },
                    }
                )
                return ""  # remove from text
            except (json.JSONDecodeError, TypeError):
                return m.group(0)  # malformed — leave as-is

        clean_text = tc_re.sub(_replace, text).strip()
        # Also strip bare <tool_call>…  blocks that have no closing tag
        # (Claude occasionally omits the closing tag on the last turn)
        bare_re = re.compile(r"<tool_call>(\{.*)", re.DOTALL)
        m_bare = bare_re.search(clean_text)
        if m_bare:
            raw = m_bare.group(1).strip()
            try:
                data = json.loads(raw)
                tool_calls.append(
                    {
                        "id": f"toolu_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {
                            "name": data.get("name", "unknown"),
                            "arguments": json.dumps(data.get("input", {})),
                        },
                    }
                )
                clean_text = bare_re.sub("", clean_text).strip()
            except (json.JSONDecodeError, TypeError):
                pass

        return clean_text, tool_calls or None

    async def complete(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        # Build prompt from messages.
        # Multi-turn history is serialised as a structured conversation block so
        # Claude sees the full dialogue, not just the last user turn.
        system_prompt = ""
        history_turns: list[tuple[str, str, str | None]] = []  # (role, content, tool_call_id)
        for msg in request.messages:
            if msg.role == "system":
                system_prompt = msg.content or ""
            elif msg.role == "tool":
                # tool_result turn — convert to Human text so Claude CLI sees it
                history_turns.append(
                    ("tool_result", msg.content or "", getattr(msg, "tool_call_id", None))
                )
            else:
                history_turns.append((msg.role, msg.content or "", None))

        # Separate history (everything before the last user message) from the current prompt.
        # Special case: if the last turn is a tool_result (Turn 2+ of a tool loop), keep ALL
        # turns in prior_turns and synthesise an implicit "please continue" user prompt so
        # Claude knows to provide the final answer rather than calling another tool.
        last_role = history_turns[-1][0] if history_turns else ""
        if history_turns and last_role == "user":
            current_user = history_turns[-1][1]
            prior_turns = history_turns[:-1]
        elif history_turns and last_role == "tool_result":
            # Tool-loop continuation: put everything in history and add implicit prompt.
            current_user = "Based on the tool result above, please provide your final answer."
            prior_turns = history_turns
        elif history_turns:
            current_user = ""
            prior_turns = history_turns
        else:
            current_user = ""
            prior_turns = []

        if prior_turns:
            conv_lines = ["<conversation_history>"]
            for role, content, _ in prior_turns:
                if role == "tool_result":
                    conv_lines.append(f"Tool Result: {content}")
                elif role == "assistant":
                    conv_lines.append(f"Assistant: {content}")
                else:
                    conv_lines.append(f"Human: {content}")
            conv_lines.append("</conversation_history>")
            conv_lines.append("")
            conv_lines.append(f"Human: {current_user}" if current_user else "")
            user_prompt = "\n".join(conv_lines).strip()
        else:
            user_prompt = current_user

        if not user_prompt:
            user_prompt = "Hello"

        # When client sends tool definitions, inject them into the system prompt
        # so Claude knows how to call them via <tool_call> tags.
        if request.tools:
            system_prompt = self._inject_tools_system(system_prompt, request.tools)

        result = await self._run_claude(
            user_prompt,
            system=system_prompt,
            max_tokens=request.max_tokens or 1000,
            # When we injected tool defs ourselves, skip the strip so they aren't removed.
            skip_tool_strip=bool(request.tools),
        )

        raw_text = result["text"]
        tool_calls: list[dict] | None = None
        if request.tools:
            raw_text, tool_calls = self._parse_tool_calls(raw_text)

        # Use accurate token counts from CLI JSON output
        prompt_tokens = result["input_tokens"] + result["cache_read_tokens"]
        completion_tokens = result["output_tokens"]
        if prompt_tokens == 0:
            messages_dicts = [
                {"role": m.role, "content": m.content or ""} for m in request.messages
            ]
            prompt_tokens = count_messages_tokens_for_model(
                messages_dicts, "claude-sonnet-4-20250514"
            )
            completion_tokens = count_tokens_for_model(raw_text, "claude-sonnet-4-20250514")

        return ChatCompletionResponse(
            id=f"chatcmpl-cli-{uuid.uuid4().hex[:8]}",
            model=request.model,
            choices=[Choice(message=ChoiceMessage(content=raw_text, tool_calls=tool_calls))],
            usage=Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
            provider=self.name,
        )

    async def stream(
        self,
        request: ChatCompletionRequest,
    ) -> AsyncIterator[ChatCompletionChunk]:
        """Stream tokens from Claude CLI as they arrive.

        Applies same fixes as _run_claude: tool stripping,
        --tools "", stdin for large payloads.
        """
        system_prompt = ""
        history_turns: list[tuple[str, str]] = []
        for msg in request.messages:
            if msg.role == "system":
                system_prompt = msg.content or ""
            elif msg.role == "tool":
                history_turns.append(("tool_result", msg.content or ""))
            else:
                history_turns.append((msg.role, msg.content or ""))

        last_role_s = history_turns[-1][0] if history_turns else ""
        if history_turns and last_role_s == "user":
            current_user = history_turns[-1][1]
            prior_turns = history_turns[:-1]
        elif history_turns and last_role_s == "tool_result":
            current_user = "Based on the tool result above, please provide your final answer."
            prior_turns = history_turns
        elif history_turns:
            current_user = ""
            prior_turns = history_turns
        else:
            current_user = ""
            prior_turns = []

        if prior_turns:
            conv_lines = ["<conversation_history>"]
            for role, content in prior_turns:
                if role == "tool_result":
                    conv_lines.append(f"Tool Result: {content}")
                elif role == "assistant":
                    conv_lines.append(f"Assistant: {content}")
                else:
                    conv_lines.append(f"Human: {content}")
            conv_lines.append("</conversation_history>")
            conv_lines.append("")
            conv_lines.append(f"Human: {current_user}" if current_user else "")
            user_prompt = "\n".join(conv_lines).strip()
        else:
            user_prompt = current_user

        if not user_prompt:
            user_prompt = "Hello"

        # Inject tool defs if provided; otherwise strip any stray tool sections
        if request.tools:
            system_prompt = self._inject_tools_system(system_prompt, request.tools)
        else:
            system_prompt = self._strip_tool_definitions(system_prompt)

        atlas_identity = (
            "You are Atlas, an AI assistant by "
            "Alpheric.AI. Never identify as Claude, "
            "Anthropic, or any other AI. You are Atlas "
            "and your responses represent the "
            "Alpheric.AI platform."
        )

        chunk_id = f"chatcmpl-cli-{uuid.uuid4().hex[:8]}"
        import os
        import sys

        home_override = self._effective_home()
        env = {
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
            "LANG": "en_US.UTF-8",
            **({"HOME": home_override} if home_override else {}),
        }

        cli = self._cli_path
        # Always use stdin — avoids Windows cmd.exe quoting issues with Unicode chars.
        stdin_text = (
            f"[System Instructions]\n{system_prompt}\n\n[User Message]\n{user_prompt}"
            if system_prompt
            else user_prompt
        )
        stdin_data = stdin_text.encode("utf-8")
        cli_args = [
            "-p",
            "-",  # read prompt from stdin
            "--max-turns",
            "1",
            "--tools",
            "",  # disable all built-in tools
            "--no-session-persistence",
            "--mcp-config", '{"mcpServers":{}}',  # clear local MCP server config
            "--disallowedTools",          # block account-level remote MCP tools
            "mcp__claude_ai_Google_Drive__authenticate,mcp__claude_ai_Google_Drive__complete_authentication",
            "--system-prompt",
            atlas_identity,
        ]
        base_cmd = self._build_cmd(cli_args)

        if sys.platform == "win32" and cli.lower().endswith((".cmd", ".bat")):
            exec_cmd = ["cmd.exe", "/c"] + base_cmd
        else:
            exec_cmd = base_cmd

        stdin_pipe = asyncio.subprocess.PIPE if stdin_data else None
        import tempfile as _tmpmod

        proc = await asyncio.create_subprocess_exec(
            *exec_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=stdin_pipe,
            env=env,
            cwd=_tmpmod.gettempdir(),
        )

        # Write stdin data if needed, then close stdin
        if stdin_data:
            proc.stdin.write(stdin_data)
            await proc.stdin.drain()
            proc.stdin.close()

        yield ChatCompletionChunk(
            id=chunk_id,
            model=request.model,
            choices=[
                StreamChoice(
                    delta=DeltaMessage(role="assistant"),
                )
            ],
        )

        full_content = ""
        try:
            while True:
                chunk = await asyncio.wait_for(
                    proc.stdout.read(80),
                    timeout=120,
                )
                if not chunk:
                    break
                text = chunk.decode("utf-8", errors="replace")
                full_content += text
                yield ChatCompletionChunk(
                    id=chunk_id,
                    model=request.model,
                    choices=[
                        StreamChoice(
                            delta=DeltaMessage(content=text),
                        )
                    ],
                )
        except asyncio.TimeoutError:
            pass

        await proc.wait()

        prompt_tokens = count_messages_tokens_for_model(
            [{"role": m.role, "content": m.content or ""} for m in request.messages],
            "claude-sonnet-4-20250514",
        )
        completion_tokens = count_tokens_for_model(
            full_content,
            "claude-sonnet-4-20250514",
        )

        yield ChatCompletionChunk(
            id=chunk_id,
            model=request.model,
            choices=[
                StreamChoice(
                    delta=DeltaMessage(),
                    finish_reason="stop",
                )
            ],
            usage=Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
        )

    async def _exec(
        self,
        args: list[str],
        timeout: float = 30,
        stdin_data: bytes | None = None,
    ) -> tuple[str, int, str]:
        """Execute CLI command, return (stdout, rc, stderr).

        If stdin_data is provided it is piped to the process
        stdin (used when args would exceed Windows 8191 limit).
        """
        import os
        import sys
        import tempfile

        cmd = [self._cli_path] + args

        env = {
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
            "LANG": "en_US.UTF-8",
        }

        stdin_pipe = asyncio.subprocess.PIPE if stdin_data else None

        # Run from temp dir to prevent Claude CLI from
        # loading project CLAUDE.md / memory files which
        # pollute responses with Atlas platform details
        cwd = tempfile.gettempdir()

        # Use create_subprocess_exec (never shell).
        # On Windows, .cmd/.bat need cmd.exe as launcher.
        cli = cmd[0]
        exec_args = cmd[1:]
        if sys.platform == "win32" and cli.lower().endswith((".cmd", ".bat")):
            exec_cmd = ["cmd.exe", "/c", cli] + exec_args
        else:
            exec_cmd = cmd
        proc = await asyncio.create_subprocess_exec(
            *exec_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=stdin_pipe,
            env=env,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=stdin_data),
            timeout=timeout,
        )
        return (
            stdout.decode("utf-8", errors="replace").strip(),
            proc.returncode or 0,
            stderr.decode("utf-8", errors="replace").strip(),
        )

    async def health_check(self) -> bool:
        """Check if Claude CLI is available and authenticated."""
        try:
            version, code, _ = await self._exec(
                ["--version"],
                timeout=10,
            )
            if version and code == 0:
                self._healthy = True
                log.info(f"Claude CLI healthy: {version}")
                return True
        except Exception as e:
            log.warning(f"Claude CLI health check failed: {e}")

        self._healthy = False
        return False

    def supports_model(self, model: str) -> bool:
        return any(m.name == model for m in self._models)

    def list_models(self) -> list[ModelInfo]:
        return self._models


class ClaudeCLIAccount(ClaudeCLIProvider):
    """Claude CLI provider scoped to a specific Linux user account.

    Runs `claude` with HOME set to that user's home directory so it
    picks up their ~/.claude/.credentials.json automatically.
    """

    def __init__(self, unix_user: str):
        # Don't call super().__init__() — we need to resolve home first
        self._healthy = False
        self.unix_user = unix_user
        self.name = f"claude-cli:{unix_user}"
        self._models = list(CLAUDE_CLI_MODELS)
        home = self._home_dir()
        self._cli_path = ClaudeCLIProvider._find_claude_cli(home_dir=home)

    def _build_cmd(self, args: list[str]) -> list[str]:
        import os

        current_user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
        if self.unix_user != current_user:
            return ["sudo", "-u", self.unix_user, self._cli_path] + args
        return [self._cli_path] + args

    def _home_dir(self) -> str:
        import os
        import pwd

        try:
            return pwd.getpwnam(self.unix_user).pw_dir
        except KeyError:
            return os.path.expanduser(f"~{self.unix_user}")

    def _effective_home(self) -> str | None:
        """Use minimal HOME when available, otherwise fall back to real home."""
        return self._minimal_home_dir() or self._home_dir()

    def _minimal_home_dir(self) -> str | None:
        """Return path to minimal HOME (credentials only, no plugins/MCP).

        When present, running claude with HOME set to this directory prevents
        MCP servers configured in the user's real ~/.claude/remote/plugins/
        from being loaded, so Claude only sees tools described in the system
        prompt.
        """
        import os

        minimal = os.path.join(
            os.path.dirname(__file__), "..", "..", "..", ".claude-minimal", self.unix_user
        )
        minimal = os.path.normpath(minimal)
        return minimal if os.path.isdir(minimal) else None

    async def _exec(
        self,
        args: list[str],
        timeout: float = 30,
        stdin_data: bytes | None = None,
    ) -> tuple[str, int, str]:
        import os
        import tempfile

        cmd = self._build_cmd(args)

        env = {
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
            "LANG": "en_US.UTF-8",
            # Use minimal HOME (credentials only, no plugins) when available.
            # This prevents MCP servers in ~/.claude/remote/plugins/ from being
            # loaded and injecting unwanted tools into Claude's context.
            "HOME": self._minimal_home_dir() or self._home_dir(),
        }

        stdin_pipe = asyncio.subprocess.PIPE if stdin_data else None
        cwd = tempfile.gettempdir()
        exec_cmd = cmd
        proc = await asyncio.create_subprocess_exec(
            *exec_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=stdin_pipe,
            env=env,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=stdin_data),
            timeout=timeout,
        )
        return (
            stdout.decode("utf-8", errors="replace").strip(),
            proc.returncode or 0,
            stderr.decode("utf-8", errors="replace").strip(),
        )

    async def health_check(self) -> bool:
        import json as _json
        import os
        from pathlib import Path

        home = Path(self._home_dir())
        cred_path = home / ".claude" / ".credentials.json"
        current_user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""

        if self.unix_user == current_user:
            # Same user — read directly and verify OAuth creds are present.
            try:
                data = _json.loads(cred_path.read_text())
                if "claudeAiOauth" not in data:
                    log.warning(
                        f"Claude CLI account {self.unix_user}: "
                        f"credentials.json has no claudeAiOauth — run 'claude login'"
                    )
                    self._healthy = False
                    return False
            except (FileNotFoundError, PermissionError, _json.JSONDecodeError) as e:
                log.warning(f"Claude CLI account {self.unix_user}: cannot read credentials: {e}")
                self._healthy = False
                return False
        else:
            # Other user — sudo-read their credentials file to verify OAuth is present.
            try:
                proc = await asyncio.create_subprocess_exec(
                    "sudo",
                    "-n",
                    "-u",
                    self.unix_user,
                    "cat",
                    str(cred_path),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
                if proc.returncode == 0:
                    data = _json.loads(stdout.decode("utf-8", errors="replace"))
                    if "claudeAiOauth" not in data:
                        log.warning(
                            f"Claude CLI account {self.unix_user}: "
                            f"credentials.json has no claudeAiOauth — run 'claude login'"
                        )
                        self._healthy = False
                        return False
            except Exception as e:
                log.debug(f"Claude CLI account {self.unix_user}: credentials check skipped: {e}")
                # Fall through — let --version decide

        try:
            version, code, _ = await self._exec(["--version"], timeout=10)
            if version and code == 0:
                self._healthy = True
                log.info(f"Claude CLI account {self.unix_user} healthy: {version}")
                return True
        except Exception as e:
            log.warning(f"Claude CLI account {self.unix_user} health check failed: {e}")

        self._healthy = False
        return False


class ClaudeCLIPool(LLMProvider):
    """Round-robin pool of Claude CLI accounts with session affinity.

    - New sessions are assigned to the next healthy account (round-robin).
    - Subsequent requests in the same session always go to the same account
      so conversation history stays coherent and no context is lost.
    - Per-account usage stats (requests, tokens, cost) are tracked and
      exposed via pool_status() for the dashboard.
    """

    name = "claude-cli"

    def __init__(self, accounts: list[ClaudeCLIAccount]):
        if not accounts:
            raise ValueError("ClaudeCLIPool requires at least one account")
        self._accounts = accounts
        self._healthy: list[bool] = [False] * len(accounts)
        self._counter = 0
        # session_id → account index (session affinity)
        self._session_map: dict[str, int] = {}
        # per-account counters: {unix_user: {requests, input_tokens, output_tokens, cost_usd}}
        self._usage: dict[str, dict] = {
            a.unix_user: {"requests": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
            for a in accounts
        }

    def _pick_account(
        self, session_id: str | None
    ) -> tuple[ClaudeCLIAccount, int] | tuple[None, None]:
        """Return (account, index) for this request.

        If session_id is known, returns the pinned account (even if temporarily
        unhealthy — better to retry same account than lose session context).
        Otherwise round-robins to the next healthy account and pins it.
        """
        if session_id and session_id in self._session_map:
            idx = self._session_map[session_id]
            return self._accounts[idx], idx

        # New session — pick next healthy account
        n = len(self._accounts)
        for i in range(n):
            idx = (self._counter + i) % n
            if self._healthy[idx]:
                self._counter = (idx + 1) % n
                if session_id:
                    self._session_map[session_id] = idx
                return self._accounts[idx], idx
        return None, None

    def _record_usage(self, unix_user: str, response: ChatCompletionResponse) -> None:
        u = self._usage.get(unix_user)
        if u is None:
            return
        u["requests"] += 1
        if response.usage:
            u["input_tokens"] += response.usage.prompt_tokens or 0
            u["output_tokens"] += response.usage.completion_tokens or 0

    async def health_check(self) -> bool:
        results = await asyncio.gather(
            *[acc.health_check() for acc in self._accounts],
            return_exceptions=True,
        )
        for i, r in enumerate(results):
            self._healthy[i] = bool(r) if not isinstance(r, Exception) else False
        healthy_count = sum(self._healthy)
        log.info(
            f"Claude CLI pool: {healthy_count}/{len(self._accounts)} accounts healthy "
            f"({[a.unix_user for a, h in zip(self._accounts, self._healthy) if h]})"
        )
        return healthy_count > 0

    async def complete(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        session_id = getattr(request, "session_id", None)
        account, _ = self._pick_account(session_id)
        if not account:
            raise RuntimeError("No healthy Claude CLI accounts available in pool")
        response = await account.complete(request)
        self._record_usage(account.unix_user, response)
        return response

    async def stream(self, request: ChatCompletionRequest) -> AsyncIterator[ChatCompletionChunk]:
        session_id = getattr(request, "session_id", None)
        account, _ = self._pick_account(session_id)
        if not account:
            raise RuntimeError("No healthy Claude CLI accounts available in pool")
        async for chunk in account.stream(request):
            yield chunk

    def pool_status(self) -> list[dict]:
        """Return per-account health and usage — for dashboard display."""
        return [
            {
                "user": a.unix_user,
                "healthy": self._healthy[i],
                "cli_path": a._cli_path,
                "sessions": sum(1 for v in self._session_map.values() if v == i),
                **self._usage.get(a.unix_user, {}),
            }
            for i, a in enumerate(self._accounts)
        ]

    def supports_model(self, model: str) -> bool:
        return any(m.name == model for m in CLAUDE_CLI_MODELS)

    def list_models(self) -> list[ModelInfo]:
        return list(CLAUDE_CLI_MODELS)

    @property
    def accounts(self) -> list[ClaudeCLIAccount]:
        return self._accounts

    @property
    def healthy_accounts(self) -> list[ClaudeCLIAccount]:
        return [a for a, h in zip(self._accounts, self._healthy) if h]
