"""Proxy router — aggregates all sub-routers into a single FastAPI router.

Sub-routers:
  openai_router      — /v1/chat/completions, /v1/models
  embeddings_router  — /v1/embeddings
  messages_router    — /v1/messages  (Anthropic Messages API — Claude Code, Cline, Zed)
  responses_router   — /v1/responses (OpenAI Responses API — OpenClaw, Paperclip)
  atlas_router       — /atlas, /atlas/models
"""

from fastapi import APIRouter

from a1.proxy.atlas_router import router as _atlas
from a1.proxy.batch_router import router as _batch
from a1.proxy.embeddings_router import router as _embeddings
from a1.proxy.files_router import router as _files
from a1.proxy.messages_router import router as _messages
from a1.proxy.openai_router import router as _openai
from a1.proxy.responses_router import router as _responses
from a1.vectorstore.router import router as _vectorstore

router = APIRouter(tags=["proxy"])
router.include_router(_openai)
router.include_router(_embeddings)
router.include_router(_files)
router.include_router(_vectorstore)
router.include_router(_batch)
router.include_router(_messages)
router.include_router(_responses)
router.include_router(_atlas)
