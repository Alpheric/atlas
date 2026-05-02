"""Proxy router — aggregates all sub-routers into a single FastAPI router.

Sub-routers:
  openai_router    — /v1/chat/completions, /v1/models
  messages_router  — /v1/messages  (Anthropic Messages API — Claude Code, Cline, Zed)
  responses_router — /v1/responses (OpenAI Responses API — OpenClaw, Paperclip)
  atlas_router     — /atlas, /atlas/models
"""

from fastapi import APIRouter

from a1.proxy.atlas_router import router as _atlas
from a1.proxy.messages_router import router as _messages
from a1.proxy.openai_router import router as _openai
from a1.proxy.responses_router import router as _responses

router = APIRouter(tags=["proxy"])
router.include_router(_openai)
router.include_router(_messages)
router.include_router(_responses)
router.include_router(_atlas)
