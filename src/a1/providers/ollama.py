"""Ollama provider with multi-server support.

Discovers and routes to models across multiple Ollama servers
(e.g., 10.0.0.9 for code models, 10.0.0.10 for QA/reasoning models).
"""

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx

from a1.common.logging import get_logger
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
from config.settings import settings

log = get_logger("providers.ollama")


@dataclass
class OllamaServer:
    url: str
    name: str
    models: list[ModelInfo]
    healthy: bool = True


class OllamaProvider(LLMProvider):
    """Multi-server Ollama provider. Discovers models across all configured servers."""

    name = "ollama"

    def __init__(self):
        self._servers: list[OllamaServer] = []
        self._model_to_server: dict[str, OllamaServer] = {}
        self._models: list[ModelInfo] = []
        # Persistent per-server HTTP clients — reuses TCP connections instead of
        # opening a new socket per request (saves ~50-100ms per call).
        self._clients: dict[str, httpx.AsyncClient] = {}

    async def discover_models(self):
        """Discover models from all configured Ollama servers."""
        urls = list(settings.ollama_servers) if settings.ollama_servers else []
        # Always include the primary URL if not already in the list
        if settings.ollama_base_url and settings.ollama_base_url not in urls:
            urls.insert(0, settings.ollama_base_url)

        self._servers.clear()
        self._model_to_server.clear()
        self._models.clear()

        for url in urls:
            server = OllamaServer(url=url, name=url, models=[])
            try:
                async with httpx.AsyncClient(base_url=url, timeout=10.0) as client:
                    resp = await client.get("/api/tags")
                    resp.raise_for_status()
                    data = resp.json()

                    for m in data.get("models", []):
                        model_name = m["name"]
                        details = m.get("details", {})
                        model_info = ModelInfo(
                            name=model_name,
                            provider="ollama",
                            context_window=details.get("context_length", 4096),
                            cost_per_1k_input=0.0,
                            cost_per_1k_output=0.0,
                            supports_tools=True,
                            supports_streaming=True,
                        )
                        server.models.append(model_info)
                        self._models.append(model_info)
                        # Map model to its server (first server wins if duplicated)
                        if model_name not in self._model_to_server:
                            self._model_to_server[model_name] = server

                    server.healthy = True
                    log.info(
                        f"Ollama server {url}: discovered {len(server.models)} models "
                        f"— {[m.name for m in server.models]}"
                    )

            except Exception as e:
                server.healthy = False
                log.warning(f"Ollama server {url}: unreachable — {e}")

            self._servers.append(server)

        total = len(self._models)
        healthy = sum(1 for s in self._servers if s.healthy)
        log.info(f"Ollama: {total} models across {healthy}/{len(self._servers)} servers")
        # Pre-warm models into VRAM so the first real request has no cold-start delay.
        await self._warm_up_models()

    async def _warm_up_models(self):
        """Send a minimal generation to each server to load models into VRAM."""
        import asyncio
        tasks = []
        for server in self._servers:
            if not server.healthy or not server.models:
                continue
            client, _ = self._get_client_for_model(server.models[0].name)
            # num_predict=1 generates one token — just enough to load weights into VRAM.
            payload = {
                "model": server.models[0].name,
                "prompt": "hi",
                "stream": False,
                "options": {"num_predict": 1, "keep_alive": -1},
            }
            tasks.append(client.post("/api/generate", json=payload))
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    log.warning(f"Warm-up failed for server {i}: {r}")
                else:
                    log.info(f"Warm-up done for server {i}")

    def _get_client_for_model(self, model: str) -> tuple[httpx.AsyncClient, str]:
        """Return the persistent HTTP client for the server hosting this model."""
        server = self._model_to_server.get(model)
        if not server:
            for s in self._servers:
                if s.healthy and s.models:
                    server = s
                    break
        url = server.url if server else settings.ollama_base_url
        if url not in self._clients:
            self._clients[url] = httpx.AsyncClient(
                base_url=url,
                timeout=httpx.Timeout(connect=5.0, read=300.0, write=30.0, pool=5.0),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        return self._clients[url], url

    def get_server_for_model(self, model: str) -> str:
        """Get the server URL for a model (for display/logging)."""
        server = self._model_to_server.get(model)
        return server.url if server else settings.ollama_base_url

    @staticmethod
    def _is_thinking_model(model: str) -> bool:
        """Return True for models that use a chain-of-thought <think> phase before producing
        visible content (e.g. deepseek-r1, qwq).  These need a larger token budget so the
        thinking phase can complete before the actual answer is emitted."""
        name = model.lower()
        return any(k in name for k in ("deepseek-r1", "deepseek_r1", "qwq", "r1"))

    def _build_options(self, request: ChatCompletionRequest, model: str = "") -> dict:
        """Build Ollama options dict from the request."""
        # Thinking models (deepseek-r1, qwq) spend hundreds of tokens on internal
        # reasoning before emitting any visible content.  Enforce a safe floor so they
        # don't get cut off mid-think.
        thinking = self._is_thinking_model(model)
        min_tokens = 4096 if thinking else 512
        opts: dict = {
            # Keep model loaded indefinitely — eliminates cold-start reload delay.
            "keep_alive": -1,
            # Limit output to what was requested so Ollama doesn't over-generate.
            "num_predict": max(request.max_tokens or 2048, min_tokens),
        }
        if request.temperature is not None:
            opts["temperature"] = request.temperature
        return opts

    async def complete(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        model = request.model
        if model == "local" and self._models:
            model = self._models[0].name

        client, _ = self._get_client_for_model(model)
        messages = [{"role": m.role, "content": m.content or ""} for m in request.messages]
        payload = {"model": model, "messages": messages, "stream": False,
                   "options": self._build_options(request, model)}

        resp = await client.post("/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()

        msg = data.get("message", {})
        # Thinking models (deepseek-r1, qwq) emit reasoning in a separate `thinking`
        # field and may leave `content` empty.  Fall back so callers always get text.
        content = msg.get("content") or msg.get("thinking") or ""

        return ChatCompletionResponse(
            model=model,
            choices=[Choice(message=ChoiceMessage(content=content))],
            usage=Usage(
                prompt_tokens=data.get("prompt_eval_count", 0),
                completion_tokens=data.get("eval_count", 0),
                total_tokens=data.get("prompt_eval_count", 0) + data.get("eval_count", 0),
            ),
            provider=self.name,
        )

    async def stream(self, request: ChatCompletionRequest) -> AsyncIterator[ChatCompletionChunk]:
        model = request.model
        if model == "local" and self._models:
            model = self._models[0].name

        client, _ = self._get_client_for_model(model)
        messages = [{"role": m.role, "content": m.content or ""} for m in request.messages]
        payload = {"model": model, "messages": messages, "stream": True,
                   "options": self._build_options(request, model)}
        chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

        yield ChatCompletionChunk(
            id=chunk_id,
            model=model,
            choices=[StreamChoice(delta=DeltaMessage(role="assistant"))],
        )

        async with client.stream("POST", "/api/chat", json=payload) as resp:
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                data = json.loads(line)
                if data.get("done"):
                    yield ChatCompletionChunk(
                        id=chunk_id,
                        model=model,
                        choices=[StreamChoice(delta=DeltaMessage(), finish_reason="stop")],
                        usage=Usage(
                            prompt_tokens=data.get("prompt_eval_count", 0),
                            completion_tokens=data.get("eval_count", 0),
                            total_tokens=data.get("prompt_eval_count", 0)
                            + data.get("eval_count", 0),
                        ),
                    )
                    break
                msg_chunk = data.get("message", {})
                # Thinking models emit reasoning in `thinking`; surface it so the
                # stream isn't empty while the model works through its chain-of-thought.
                content = msg_chunk.get("content") or msg_chunk.get("thinking") or ""
                if content:
                    yield ChatCompletionChunk(
                        id=chunk_id,
                        model=model,
                        choices=[StreamChoice(delta=DeltaMessage(content=content))],
                    )

    async def health_check(self) -> bool:
        return any(s.healthy for s in self._servers)

    def supports_model(self, model: str) -> bool:
        if model == "local":
            return True
        return any(m.name == model for m in self._models)

    def list_models(self) -> list[ModelInfo]:
        return self._models

    def list_servers(self) -> list[dict]:
        """Return server status for the dashboard."""
        return [
            {
                "url": s.url,
                "name": s.name,
                "healthy": s.healthy,
                "models": [m.name for m in s.models],
                "model_count": len(s.models),
            }
            for s in self._servers
        ]
