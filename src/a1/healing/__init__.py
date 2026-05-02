"""Self-Heal Model — automatic response quality improvement.

Layers:
  1. quality_scorer   — fast heuristic score (0.0–1.0) on every response
  2. self_critique    — Claude rewrites its own low-quality responses (non-streaming)
  3. feedback_handler — thumbs-down → regenerate + training pair (Phase 2)
  4. conversation_monitor — background health scan (Phase 3)
"""
