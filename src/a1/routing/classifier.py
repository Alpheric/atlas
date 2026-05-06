"""Task type classifier. Starts rule-based, upgrades to ML when enough data exists."""

import hashlib
import re
import time

from a1.proxy.request_models import ChatCompletionRequest
from a1.routing.features import RequestFeatures, extract_features

# ---------------------------------------------------------------------------
# 4.2 — Model-based fallback cache (in-process, 60s TTL)
# ---------------------------------------------------------------------------
_llm_cache: dict[str, tuple[str, float]] = {}
_CACHE_TTL = 60.0

_CLASSIFIER_PROMPT = (
    "Classify the following request into exactly one task type. "
    "Output only the task type, nothing else.\n\n"
    "Valid types: coding, general, security, reasoning, documents, data, infra\n\n"
    "Request: {text}\n\nTask type:"
)


TASK_TYPES = [
    "coding",
    "general",
    "security",
    "reasoning",
    "documents",
    "data",
    "infra",
]

# P1: Security keyword pattern — triggers security classification regardless of message length
SECURITY_ANALYSIS_PATTERNS = re.compile(
    r"vulnerability|CVE-\d|threat\s+model|audit|anomaly|dataset\b|data\s+leak|"
    r"pentest|sql\s+injection|xss|csrf|attack\s+surface|compliance\b|"
    r"intrusion|malware|exploit|breach|incident\s+response",
    re.IGNORECASE,
)


def _feature_confidence(features: RequestFeatures) -> float:
    """Compute confidence from feature match strength.

    Counts how many signal features are active and normalises to [0.4, 0.95].
    Replaces hardcoded constants so stored confidence values reflect real input signals.
    """
    active = sum(
        [
            features.has_code_markers,
            features.has_tools,
            features.has_math_markers,
            features.has_translation_cues,
            features.has_summarization_cues,
            features.has_structured_output_cues,
            features.has_question_markers,
            features.has_system_prompt,
        ]
    )
    total = 8
    # Scale: 0 active features → 0.4, all active → 0.95
    return round(0.4 + 0.55 * (active / total), 3)


async def _llm_classify(text: str) -> str | None:
    """Ask llama3.2:latest to classify; returns task_type or None on any error."""
    cache_key = hashlib.md5(text.encode()).hexdigest()
    now = time.monotonic()
    cached = _llm_cache.get(cache_key)
    if cached and cached[1] > now:
        return cached[0]

    try:
        import httpx

        from config.settings import settings

        prompt = _CLASSIFIER_PROMPT.format(text=text[:600])
        payload = {
            "model": "llama3.2:latest",
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0, "num_predict": 10},
        }
        base = (
            settings.ollama_servers[0].rstrip("/")
            if settings.ollama_servers
            else "http://10.0.0.9:11434"
        )
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(f"{base}/api/generate", json=payload)
            resp.raise_for_status()
            raw = resp.json().get("response", "").strip().lower()
    except Exception:
        return None

    for tt in TASK_TYPES:
        if tt in raw:
            _llm_cache[cache_key] = (tt, now + _CACHE_TTL)
            return tt
    return None


async def classify_task_with_fallback(request: ChatCompletionRequest) -> tuple[str, float]:
    """classify_task + LLM second-pass when rule-based confidence < 0.70."""
    task_type, confidence = classify_task(request)
    if confidence >= 0.70:
        return task_type, confidence
    full_text = " ".join(m.content or "" for m in request.messages)
    llm_type = await _llm_classify(full_text)
    if llm_type:
        return llm_type, 0.70
    return task_type, confidence


def classify_task(request: ChatCompletionRequest) -> tuple[str, float]:
    """Classify the task type. Returns (task_type, confidence).

    Confidence is computed from feature match strength, not hardcoded constants,
    so stored routing_decisions values are usable as a scorer training signal.
    """
    features = extract_features(request)
    conf = _feature_confidence(features)

    # Rule-based classification (Tier 1)
    # Priority order matters — more specific patterns first

    if features.has_tools and features.has_code_markers:
        return "coding", conf

    # Code markers → coding task (short or long)
    if features.has_code_markers:
        return "coding", conf

    # P1: Security keywords take priority over math/translation/etc.
    # (e.g. "CVE-2023-1234" falsely triggers math via the subtraction regex)
    full_text = " ".join(m.content or "" for m in request.messages)
    if SECURITY_ANALYSIS_PATTERNS.search(full_text):
        return "security", conf

    if features.has_math_markers:
        return "reasoning", conf

    if features.has_translation_cues:
        return "documents", conf

    if features.has_summarization_cues:
        return "documents", conf

    if features.has_structured_output_cues:
        return "data", conf

    if features.has_tools:
        return "coding", conf  # tool use often implies agentic/code tasks

    if features.token_count_bucket == "very_long" and features.has_system_prompt:
        return "reasoning", conf

    return "general", conf
