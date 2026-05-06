"""Search intent detector.

Scores how likely it is that a user message requires live web search.
Uses a weighted keyword / pattern approach — no ML, sub-millisecond latency.

Score 0-100:
  < 30  → no search needed
  30-59 → optional (log but don't search by default)
  ≥ 60  → search recommended
"""

import re

from a1.common.logging import get_logger

log = get_logger("search.intent")

# ---------------------------------------------------------------------------
# Weighted trigger patterns — (pattern, score_contribution)
# ---------------------------------------------------------------------------

# Strong signals (25 pts each) — almost always need live data
_STRONG_TRIGGERS: list[tuple[re.Pattern, int]] = [
    (re.compile(r"\b(latest|current|recent|up.?to.?date|real.?time|live)\b", re.I), 25),
    (re.compile(r"\btoday|right now|this week|this month|as of \d{4}\b", re.I), 25),
    (re.compile(r"\b(news|headline|breaking|just (released|announced|launched))\b", re.I), 25),
    (re.compile(r"\b(search|look up|google|find online|browse the web)\b", re.I), 30),  # explicit request
    (re.compile(r"\b(cite|citation|source|reference|according to|link me)\b", re.I), 25),
]

# Medium signals (15 pts each) — often need fresh data
_MEDIUM_TRIGGERS: list[tuple[re.Pattern, int]] = [
    (re.compile(r"\b(price|cost|how much|pricing|fee|rate)\b", re.I), 15),
    (re.compile(r"\b(law|regulation|policy|rule|legislation|compliance|gdpr|hipaa)\b", re.I), 15),
    (re.compile(r"\b(version|release|changelog|update|patch)\b", re.I), 15),
    (re.compile(r"\b(who is|who's|founder|ceo|president|leadership|team of)\b", re.I), 15),
    (re.compile(r"\b(company|startup|organization|firm|corporation)\b", re.I), 10),
    (re.compile(r"\b(product spec|feature list|documentation for)\b", re.I), 15),
    (re.compile(r"\b(stock|market|crypto|bitcoin|exchange rate|nifty|dow)\b", re.I), 20),
    (re.compile(r"\bweather\b", re.I), 20),
]

# Weak signals (8 pts each) — occasionally need fresh data
_WEAK_TRIGGERS: list[tuple[re.Pattern, int]] = [
    (re.compile(r"\b(what is|tell me about|explain|describe)\b", re.I), 5),
    (re.compile(r"\b(compare|versus|vs\.?)\b", re.I), 5),
    (re.compile(r"\b(best|top|recommended|popular)\b", re.I), 8),
    (re.compile(r"\?$", re.I), 3),           # ends with a question mark
    (re.compile(r"\b(2024|2025|2026)\b"), 8),  # specific recent year mentioned
]

# Suppression patterns — if these match, reduce score (often static knowledge)
_SUPPRESS_TRIGGERS: list[tuple[re.Pattern, int]] = [
    (re.compile(r"\b(how to|tutorial|example|code|implement|write a|create a)\b", re.I), -10),
    (re.compile(r"\b(math|calculate|compute|formula|equation)\b", re.I), -15),
    (re.compile(r"\b(explain|concept|theory|definition|meaning)\b", re.I), -8),
    (re.compile(r"\b(translate|paraphrase|summarize|rewrite)\b", re.I), -20),
    (re.compile(r"\b(joke|poem|story|write me|generate|create)\b", re.I), -15),
]

_SEARCH_THRESHOLD = 50    # score >= this triggers a search (two strong signals = 50)
_OPTIONAL_THRESHOLD = 25  # score in [25, 50) — could search if configured aggressively


def score_search_intent(text: str) -> int:
    """Return a score 0-100 representing how much this text needs live web search.

    Clamps to [0, 100]. Thread-safe (pure function).
    """
    if not text or not text.strip():
        return 0

    score = 0

    for pattern, pts in _STRONG_TRIGGERS:
        if pattern.search(text):
            score += pts

    for pattern, pts in _MEDIUM_TRIGGERS:
        if pattern.search(text):
            score += pts

    for pattern, pts in _WEAK_TRIGGERS:
        if pattern.search(text):
            score += pts

    for pattern, pts in _SUPPRESS_TRIGGERS:
        if pattern.search(text):
            score += pts  # pts are negative here

    return max(0, min(100, score))


def needs_web_search(
    text: str,
    threshold: int = _SEARCH_THRESHOLD,
) -> tuple[bool, int, str]:
    """Decide whether web search is needed for this user message.

    Returns (should_search, score, reason).
    """
    score = score_search_intent(text)
    should_search = score >= threshold

    if score >= _SEARCH_THRESHOLD:
        reason = "high_intent"
    elif score >= _OPTIONAL_THRESHOLD:
        reason = "medium_intent"
    else:
        reason = "no_intent"

    return should_search, score, reason


def extract_search_query(messages: list) -> str:
    """Extract the best query string from the message list.

    Uses the last user message. Strips common preamble phrases so the
    query sent to the search API is more direct.
    """
    # Find the last user message
    user_text = ""
    for msg in reversed(messages):
        role = getattr(msg, "role", "") or (msg.get("role", "") if isinstance(msg, dict) else "")
        content = getattr(msg, "content", "") or (msg.get("content", "") if isinstance(msg, dict) else "")
        if role == "user" and content:
            user_text = content
            break

    if not user_text:
        return ""

    # Remove common preamble that doesn't help the search engine
    preambles = [
        r"^(please|can you|could you|would you|i need|i want|i'd like|tell me|show me|find me)\s+",
        r"^(search for|look up|google|find information (about|on))\s+",
        r"^(what is|what are|who is|who are|where is|when is)\s+",
    ]
    query = user_text.strip()
    for p in preambles:
        query = re.sub(p, "", query, flags=re.I).strip()

    # Cap at 200 characters — most search APIs have limits
    return query[:200]
