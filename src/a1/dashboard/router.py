"""Admin dashboard API — thin aggregator that includes all sub-routers.

Sub-routers:
  analytics_router    — overview, metrics, timeseries, routing, model compare
  conversations_router — conversations, sessions, feedback, PII stats
  training_router     — training runs, distillation, Argilla, import
  providers_router    — providers, accounts, Ollama, servers, playground
  agents_router       — agents, applications, workspaces
  plans_router        — planning API

The WebSocket live-feed stays here because it has special auth handling
(query-param token instead of Authorization header).
"""

import json

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from a1.common.auth import verify_api_key
from a1.dashboard.agents_router import router as agents
from a1.dashboard.analytics_router import router as analytics
from a1.dashboard.auth_router import router as auth
from a1.dashboard.conversations_router import router as conversations
from a1.dashboard.eval_router import router as eval_router
from a1.dashboard.governance_router import router as governance
from a1.dashboard.plans_router import router as plans
from a1.dashboard.prompts_router import router as prompts
from a1.dashboard.providers_router import router as providers
from a1.dashboard.training_router import router as training
from a1.dashboard.users_router import router as users
from config.settings import settings

# Public routes (auth/login must be reachable without a token).
_public_router = APIRouter(prefix="/admin", tags=["dashboard"])
_public_router.include_router(auth)

# HTTP routes: protected by the verify_api_key router-level dependency.
router = APIRouter(prefix="/admin", tags=["dashboard"], dependencies=[Depends(verify_api_key)])

router.include_router(analytics)
router.include_router(conversations)
router.include_router(training)
router.include_router(providers)
router.include_router(agents)
router.include_router(plans)
router.include_router(governance)
router.include_router(users)
router.include_router(prompts)
router.include_router(eval_router)

# WebSocket route is registered separately (no router-level dep) because browsers
# cannot send Authorization headers during a WebSocket upgrade. Auth is enforced
# inline via query-param token: ws://host/admin/ws/live-feed?token=<api_key>
_ws_router = APIRouter(prefix="/admin", tags=["dashboard"])

# --- WebSocket live feed ---
_live_connections: list[WebSocket] = []


@_ws_router.websocket("/ws/live-feed")
async def live_feed(websocket: WebSocket, token: str | None = Query(None)):
    if settings.api_keys:
        if token not in settings.api_keys:
            await websocket.close(code=1008)  # 1008 = Policy Violation
            return
    await websocket.accept()
    _live_connections.append(websocket)
    try:
        while True:
            await websocket.receive_text()  # keep alive
    except WebSocketDisconnect:
        _live_connections.remove(websocket)


async def broadcast_event(event: dict):
    """Broadcast an event to all connected dashboard clients."""
    data = json.dumps(event, default=str)
    for ws in _live_connections[:]:
        try:
            await ws.send_text(data)
        except Exception:
            _live_connections.remove(ws)
