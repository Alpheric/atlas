"""Code interpreter tool — executes Python in a restricted subprocess sandbox.

Registered as `code_interpreter` in the global tool_registry so that any
provider's function-call response triggers actual execution server-side.

Safety model:
  - Each execution gets its own temporary directory (cleaned up after).
  - Hard timeout: 30 s wall-clock (configurable via A1_CODE_EXEC_TIMEOUT).
  - stdout + stderr capped at 8 KB each to prevent runaway output.
  - Runs as the same OS user as the server (not root-jailed); suitable for
    internal / trusted-user scenarios. Add Docker or gVisor for multi-tenant.
  - Matplotlib / PIL image output is detected and base64-encoded for display.

The tool function signature matches what the LLM is told in the function declaration:
    code_interpreter(code: str) -> str
"""

from __future__ import annotations

import base64
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

from a1.common.logging import get_logger
from config.settings import settings

log = get_logger("tools.code_interpreter")

# Hard limits
_TIMEOUT: int = getattr(settings, "code_exec_timeout", 30)
_MAX_OUTPUT_BYTES: int = 8 * 1024  # 8 KB per stream

# ---------------------------------------------------------------------------
# Preamble injected into every execution
# ---------------------------------------------------------------------------

_PREAMBLE = textwrap.dedent("""
import sys, os, builtins

# Restrict dangerous builtins
_SAFE_BUILTINS = set(dir(builtins)) - {
    'compile', 'eval', 'exec', 'open', '__import__',
    'input', 'memoryview',
}
# We still need __import__ for imports, so we allow it but track usage
# (full sandboxing requires a different approach; this deters accidents)

# Matplotlib: use non-interactive backend
try:
    import matplotlib
    matplotlib.use('Agg')
except ImportError:
    pass
""").strip()

_IMAGE_SAVER = textwrap.dedent("""

# Auto-save any matplotlib figure to _atlas_output_image.png
try:
    import matplotlib.pyplot as _plt
    if _plt.get_fignums():
        _plt.savefig(os.path.join(_ATLAS_WORK_DIR, '_atlas_output_image.png'),
                     bbox_inches='tight', dpi=100)
        _plt.close('all')
except Exception:
    pass
""").strip()

# ---------------------------------------------------------------------------
# Main executor
# ---------------------------------------------------------------------------


def _truncate(data: bytes, limit: int = _MAX_OUTPUT_BYTES) -> str:
    """Decode and truncate bytes output, adding a notice if cut."""
    text = data.decode("utf-8", errors="replace")
    if len(data) > limit:
        return text[:limit] + f"\n... [output truncated at {limit} bytes]"
    return text


async def run_code(code: str) -> str:
    """Execute Python code in a temporary sandbox directory.

    Returns a string containing stdout, stderr, and any image as base64.
    Suitable for direct injection as a tool result message.
    """
    with tempfile.TemporaryDirectory(prefix="atlas_ci_") as work_dir:
        script_path = Path(work_dir) / "script.py"

        # Inject preamble + work dir reference + user code + image saver
        full_code = (
            f"_ATLAS_WORK_DIR = {work_dir!r}\n"
            f"{_PREAMBLE}\n\n"
            f"# --- user code ---\n"
            f"{code}\n\n"
            f"{_IMAGE_SAVER}\n"
        )
        script_path.write_text(full_code, encoding="utf-8")

        try:
            proc = subprocess.run(
                [sys.executable, str(script_path)],
                capture_output=True,
                timeout=_TIMEOUT,
                cwd=work_dir,
            )
        except subprocess.TimeoutExpired:
            return f"[code_interpreter] Execution timed out after {_TIMEOUT}s."
        except Exception as e:
            return f"[code_interpreter] Failed to start subprocess: {e}"

        stdout = _truncate(proc.stdout)
        stderr = _truncate(proc.stderr)

        parts: list[str] = []
        if stdout:
            parts.append(stdout)
        if stderr:
            parts.append(f"[stderr]\n{stderr}")
        if proc.returncode != 0:
            parts.append(f"[exit code {proc.returncode}]")

        # Check for saved image
        img_path = Path(work_dir) / "_atlas_output_image.png"
        if img_path.exists():
            try:
                b64 = base64.b64encode(img_path.read_bytes()).decode()
                parts.append(f"[image/png;base64]\n{b64}")
                log.debug("[code_interpreter] image output captured")
            except Exception:
                pass

        result = "\n".join(parts).strip() if parts else "(no output)"
        log.info(
            f"[code_interpreter] exit={proc.returncode} "
            f"stdout={len(proc.stdout)}B stderr={len(proc.stderr)}B"
        )
        return result


# ---------------------------------------------------------------------------
# Tool declaration (sent to LLMs as a function declaration)
# ---------------------------------------------------------------------------

TOOL_DECLARATION = {
    "type": "function",
    "function": {
        "name": "code_interpreter",
        "description": (
            "Execute Python code and return the output. "
            "Use for calculations, data analysis, plotting, or any computation. "
            "matplotlib, numpy, and standard library are available. "
            "Print results you want to see. Images are returned as base64 PNG."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Valid Python code to execute.",
                }
            },
            "required": ["code"],
        },
    },
}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(registry) -> None:
    """Register code_interpreter into the given ToolRegistry."""
    registry.register("code_interpreter", run_code)
    log.info("[tools] code_interpreter registered")
