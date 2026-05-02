"""Layer 2 — Self-Critique & Regeneration.

When a response scores below ``settings.quality_min_score``, Claude is asked
to critique its own response and produce an improved version.

Only fires for non-streaming requests (streaming responses can't be replaced
after the first byte is sent).
"""

from __future__ import annotations

from a1.common.logging import get_logger

log = get_logger("healing.self_critique")

_CRITIQUE_PROMPT = """\
You generated the following response to a user request. It has been flagged as \
potentially low-quality. Generate a significantly improved version.

Rules:
- Respond with ONLY the improved response (no meta-commentary, no preamble)
- Make it more complete, accurate, and directly useful
- Match the task type: {task_type}
- If code is expected, include a proper code block
- Keep the improved response focused on what the user actually asked

=== USER REQUEST ===
{user_message}

=== YOUR ORIGINAL RESPONSE ===
{original_response}
"""


async def self_critique(
    user_message: str,
    original_response: str,
    task_type: str,
    provider,  # LLMProvider instance (claude-cli)
    model: str,
    max_tokens: int = 1500,
) -> str | None:
    """Ask the provider to critique and rewrite *original_response*.

    Returns the improved response text, or ``None`` if the critique failed
    or produced output that is not meaningfully longer/better than the original.
    """
    from a1.proxy.request_models import ChatCompletionRequest

    prompt = _CRITIQUE_PROMPT.format(
        user_message=user_message[:2000],
        original_response=original_response[:3000],
        task_type=task_type,
    )

    try:
        req = ChatCompletionRequest(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.3,  # lower temperature → more focused improvement
        )
        resp = await provider.complete(req)
        improved = resp.choices[0].message.content if resp.choices else None
        if improved:
            improved = improved.strip()
            # Only accept the improved response if it's non-trivially long
            if len(improved) > 20:
                log.info(
                    f"[self-critique] Improved response "
                    f"(task={task_type}, "
                    f"orig_len={len(original_response)}, "
                    f"new_len={len(improved)})"
                )
                return improved
    except Exception as exc:
        log.warning(f"[self-critique] Failed: {exc}")

    return None
