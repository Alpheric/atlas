"""Claude CLI proxy provider — routes requests through the local Claude CLI.

Uses the `claude` command-line tool (which handles its own OAuth/auth)
to forward completion requests to Anthropic's API. This avoids needing
a separate API key since the CLI manages token refresh automatically.
"""

import asyncio
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

    async def _run_claude(self, prompt: str, system: str = "", max_tokens: int = 1000) -> dict:
        """Run the claude CLI with a prompt and return parsed JSON result.

        Returns dict with: result (text), usage (tokens), duration_api_ms, cost
        """
        # Strip tool definitions — Claude CLI can't execute
        # tools and will waste turns trying to call them
        system = self._strip_tool_definitions(system)

        # --tools "" disables ALL built-in tools
        # (correct flag; --allowedTools is an allowlist, not a blocklist)
        # --bare skips CLAUDE.md auto-discovery, auto-memory, hooks, LSP
        # --no-session-persistence stops Claude from saving sessions
        args = [
            "-p",
            prompt,
            "--max-turns",
            "1",
            "--output-format",
            "json",
            "--tools",
            "",  # disable all built-in tools so Claude responds with text only
            "--no-session-persistence",  # don't save/load sessions from disk
        ]

        # Prepend Atlas identity to system prompt.
        atlas_identity = (
            "You are Atlas, an AI assistant by Alpheric.AI. "
            "Never identify as Claude, Anthropic, or any other AI. "
            "You are Atlas and your responses represent the Alpheric.AI platform. "
            "Respond with text only. Do not use any tools."
        )

        # Always use stdin to pipe the prompt — avoids Windows cmd.exe quoting issues
        # with special characters (Unicode arrows, brackets, newlines) in the prompt.
        stdin_text = (
            f"[System Instructions]\n{system}\n\n[User Message]\n{prompt}" if system else prompt
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
            log.error(f"Claude CLI exit={code} stderr={stderr[:500] if stderr else 'empty'}")
            if not output:
                raise RuntimeError(
                    f"Claude CLI exit code {code}: {stderr[:300] if stderr else 'no output'}"
                )

        # Parse JSON response for accurate token counts
        try:
            import json

            data = json.loads(output)

            # Extract text — handle error_max_turns where
            # result may be empty because Claude tried tool_use
            text = data.get("result", "")
            if not text and data.get("subtype") == "error_max_turns":
                log.warning("Claude hit max turns (tool_use), returning partial result")
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

    async def complete(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        # Build prompt from messages.
        # Multi-turn history is serialised as a structured conversation block so
        # Claude sees the full dialogue, not just the last user turn.
        system_prompt = ""
        history_turns: list[tuple[str, str]] = []  # (role, content) pairs before last user msg
        for msg in request.messages:
            if msg.role == "system":
                system_prompt = msg.content or ""
            else:
                history_turns.append((msg.role, msg.content or ""))

        # Separate history (everything before the last user message) from the current prompt
        if history_turns and history_turns[-1][0] == "user":
            current_user = history_turns[-1][1]
            prior_turns = history_turns[:-1]
        elif history_turns:
            # Last message is not from user — treat all as prior context
            current_user = ""
            prior_turns = history_turns
        else:
            current_user = ""
            prior_turns = []

        if prior_turns:
            # Format prior conversation history so Claude sees full context
            conv_lines = ["<conversation_history>"]
            for role, content in prior_turns:
                label = "Human" if role == "user" else "Assistant"
                conv_lines.append(f"{label}: {content}")
            conv_lines.append("</conversation_history>")
            conv_lines.append("")
            conv_lines.append(f"Human: {current_user}" if current_user else "")
            user_prompt = "\n".join(conv_lines).strip()
        else:
            user_prompt = current_user

        if not user_prompt:
            user_prompt = "Hello"

        result = await self._run_claude(
            user_prompt,
            system=system_prompt,
            max_tokens=request.max_tokens or 1000,
        )

        # Use accurate token counts from CLI JSON output
        prompt_tokens = result["input_tokens"] + result["cache_read_tokens"]
        completion_tokens = result["output_tokens"]
        if prompt_tokens == 0:
            # Fallback to estimation
            messages_dicts = [
                {"role": m.role, "content": m.content or ""} for m in request.messages
            ]
            prompt_tokens = count_messages_tokens_for_model(
                messages_dicts, "claude-sonnet-4-20250514"
            )
            completion_tokens = count_tokens_for_model(result["text"], "claude-sonnet-4-20250514")

        return ChatCompletionResponse(
            id=f"chatcmpl-cli-{uuid.uuid4().hex[:8]}",
            model=request.model,
            choices=[Choice(message=ChoiceMessage(content=result["text"]))],
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
            else:
                history_turns.append((msg.role, msg.content or ""))

        if history_turns and history_turns[-1][0] == "user":
            current_user = history_turns[-1][1]
            prior_turns = history_turns[:-1]
        elif history_turns:
            current_user = ""
            prior_turns = history_turns
        else:
            current_user = ""
            prior_turns = []

        if prior_turns:
            conv_lines = ["<conversation_history>"]
            for role, content in prior_turns:
                label = "Human" if role == "user" else "Assistant"
                conv_lines.append(f"{label}: {content}")
            conv_lines.append("</conversation_history>")
            conv_lines.append("")
            conv_lines.append(f"Human: {current_user}" if current_user else "")
            user_prompt = "\n".join(conv_lines).strip()
        else:
            user_prompt = current_user

        if not user_prompt:
            user_prompt = "Hello"

        # Strip tool definitions
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

        env = {
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
            "LANG": "en_US.UTF-8",
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

    async def _exec(
        self,
        args: list[str],
        timeout: float = 30,
        stdin_data: bytes | None = None,
    ) -> tuple[str, int, str]:
        import os
        import sys
        import tempfile

        cmd = self._build_cmd(args)

        env = {
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
            "LANG": "en_US.UTF-8",
            "HOME": self._home_dir(),
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
                    "sudo", "-n", "-u", self.unix_user,
                    "cat", str(cred_path),
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

    def _pick_account(self, session_id: str | None) -> tuple[ClaudeCLIAccount, int] | tuple[None, None]:
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
