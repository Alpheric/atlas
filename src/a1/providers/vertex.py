"""Google Vertex AI / Gemini provider.

Supports two auth modes:
  - api_key   : Google AI Studio key → generativelanguage.googleapis.com/v1beta
  - service_account : Vertex AI project → aiplatform.googleapis.com/v1  (ADC)

Features:
  - True SSE streaming via streamGenerateContent?alt=sse
  - Web search grounding via googleSearch tool
  - Grounding metadata extraction → citation stubs
  - Config-driven model list (providers.yaml)
  - Model aliases (vertex_gemini_pro, vertex_gemini_flash, etc.)
  - PII masking applied before dispatch (CorePipeline handles this)
  - Full error normalization
  - Sticky-session support via CorePipeline (no special state here)
"""

import json
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import yaml

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

log = get_logger("providers.vertex")

# ---------------------------------------------------------------------------
# Model aliases  (client sends alias → provider uses canonical Gemini name)
# ---------------------------------------------------------------------------

_ALIASES: dict[str, str] = {
    # 2.5 aliases
    "vertex_gemini_2_5_pro": "gemini-2.5-pro",
    "vertex_gemini_2_5_flash": "gemini-2.5-flash",
    # 2.0 aliases
    "vertex_gemini_flash": "gemini-2.0-flash",
    "vertex_gemini_flash_lite": "gemini-2.0-flash-lite",
    # 1.5 aliases
    "vertex_gemini_pro": "gemini-1.5-pro",
    "vertex_gemini_1_5_flash": "gemini-1.5-flash",
    "vertex_gemini_1_5_pro": "gemini-1.5-pro",
    # Short names
    "gemini-pro": "gemini-2.5-pro",       # latest pro
    "gemini-flash": "gemini-2.5-flash",   # latest flash
    "gemini-latest": "gemini-2.5-pro",
}

# ---------------------------------------------------------------------------
# Grounding metadata
# ---------------------------------------------------------------------------


@dataclass
class GroundingChunk:
    uri: str
    title: str


@dataclass
class GroundingMetadata:
    chunks: list[GroundingChunk] = field(default_factory=list)
    web_search_queries: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "chunks": [{"uri": c.uri, "title": c.title} for c in self.chunks],
            "web_search_queries": self.web_search_queries,
        }


# ---------------------------------------------------------------------------
# Error normalisation
# ---------------------------------------------------------------------------

_HTTP_ERROR_MAP: dict[int, str] = {
    400: "invalid_request",
    401: "authentication_error",
    403: "permission_denied",
    404: "model_not_found",
    429: "rate_limited",
    500: "internal_error",
    503: "service_unavailable",
}


def _classify_error(status_code: int, body: str) -> str:
    if status_code == 429:
        if "quota" in body.lower():
            return "quota_exceeded"
        return "rate_limited"
    return _HTTP_ERROR_MAP.get(status_code, "unknown_error")


# ---------------------------------------------------------------------------
# Multimodal helpers
# ---------------------------------------------------------------------------


def _openai_parts_to_gemini(parts: list, fetched: dict[str, tuple[str, str]] | None = None) -> list[dict]:
    """Convert OpenAI multimodal content parts to Gemini API parts.

    OpenAI format:
      {"type": "text", "text": "..."}
      {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
      {"type": "image_url", "image_url": {"url": "https://..."}}

    Gemini format:
      {"text": "..."}
      {"inline_data": {"mime_type": "image/jpeg", "data": "<base64>"}}

    `fetched` maps url → (base64_data, mime_type) for pre-downloaded HTTP images.
    """
    gemini: list[dict] = []
    for item in parts:
        if not isinstance(item, dict):
            continue
        t = item.get("type", "")
        if t == "text":
            text = item.get("text", "")
            if text:
                gemini.append({"text": text})
        elif t == "image_url":
            url_obj = item.get("image_url", {})
            url = url_obj.get("url", "") if isinstance(url_obj, dict) else str(url_obj)
            if url.startswith("data:"):
                try:
                    header, b64 = url.split(";base64,", 1)
                    mime = header[5:]  # strip "data:"
                    gemini.append({"inline_data": {"mime_type": mime, "data": b64}})
                except ValueError:
                    pass
            elif url and fetched and url in fetched:
                b64, mime = fetched[url]
                gemini.append({"inline_data": {"mime_type": mime, "data": b64}})
            elif url:
                # URL not pre-fetched — emit placeholder so the model at least sees the text
                gemini.append({"text": f"[image: {url}]"})
    return gemini or [{"text": ""}]


async def _fetch_image_urls(messages: list) -> dict[str, tuple[str, str]]:
    """Download all HTTP image URLs in the message list, return url→(base64, mime) map."""
    import base64

    urls: set[str] = set()
    for msg in messages:
        parts = getattr(msg, "content_parts", None) if not isinstance(msg, dict) else None
        if not parts:
            continue
        for item in parts:
            if isinstance(item, dict) and item.get("type") == "image_url":
                url_obj = item.get("image_url", {})
                url = url_obj.get("url", "") if isinstance(url_obj, dict) else str(url_obj)
                if url and not url.startswith("data:"):
                    urls.add(url)

    if not urls:
        return {}

    fetched: dict[str, tuple[str, str]] = {}
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        for url in urls:
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
                b64 = base64.b64encode(resp.content).decode()
                fetched[url] = (b64, content_type)
                log.debug(f"[vertex] fetched image {url[:60]} ({content_type}, {len(resp.content)}B)")
            except Exception as e:
                log.warning(f"[vertex] failed to fetch image {url[:60]}: {e}")
    return fetched


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class VertexProvider(LLMProvider):
    """Google Vertex AI / Gemini provider.

    Auth is chosen at init time:
      - api_key mode   (A1_VERTEX_AUTH_TYPE=api_key, A1_VERTEX_API_KEY set)
      - service_account (A1_VERTEX_AUTH_TYPE=service_account, A1_VERTEX_PROJECT_ID set)
        Uses google-auth ADC if available; falls back gracefully.
    """

    name = "vertex"

    def __init__(self):
        self.auth_type: str = settings.vertex_auth_type          # "api_key" | "service_account"
        self.api_key: str = settings.vertex_api_key
        self.project_id: str = settings.vertex_project_id
        self.location: str = settings.vertex_location
        self.default_model: str = settings.vertex_default_model or "gemini-2.0-flash"
        self.web_search_enabled: bool = settings.vertex_web_search_enabled
        self.timeout: float = settings.vertex_timeout

        self._models: list[ModelInfo] = self._load_models()
        self._client = httpx.AsyncClient(timeout=self.timeout)

        # ADC token cache (service_account mode)
        self._sa_token: str | None = None
        self._sa_token_expiry: float = 0.0

    # ------------------------------------------------------------------
    # Config loading
    # ------------------------------------------------------------------

    def _load_models(self) -> list[ModelInfo]:
        try:
            with open("config/providers.yaml") as f:
                cfg = yaml.safe_load(f)
            vertex_cfg = cfg.get("providers", {}).get("vertex", {})
            models = []
            for m in vertex_cfg.get("models", []):
                models.append(
                    ModelInfo(
                        name=m["name"],
                        provider="vertex",
                        context_window=m.get("context_window", 1000000),
                        cost_per_1k_input=m.get("cost_per_1k_input", 0.0),
                        cost_per_1k_output=m.get("cost_per_1k_output", 0.0),
                        supports_tools=m.get("supports_tools", True),
                        supports_streaming=m.get("supports_streaming", True),
                        supports_vision=m.get("supports_vision", False),
                        max_output_tokens=m.get("max_output_tokens", 8192),
                        tier=m.get("tier", "standard"),
                        latency_class=m.get("latency_class", "normal"),
                    )
                )
            return models
        except Exception as e:
            log.warning(f"[vertex] Could not load models from providers.yaml: {e}")
            return [
                ModelInfo("gemini-2.0-flash", "vertex", 1000000, 0.00015, 0.0006, True, True, True),
                ModelInfo("gemini-1.5-pro", "vertex", 2000000, 0.00125, 0.005, True, True, True),
            ]

    # ------------------------------------------------------------------
    # LLMProvider interface
    # ------------------------------------------------------------------

    def supports_model(self, model: str) -> bool:
        canonical = _ALIASES.get(model, model)
        return any(m.name == canonical for m in self._models)

    def list_models(self) -> list[ModelInfo]:
        return self._models

    # ------------------------------------------------------------------
    # Endpoint + auth helpers
    # ------------------------------------------------------------------

    def _resolve_model(self, model: str) -> str:
        return _ALIASES.get(model, model) or self.default_model

    def _api_key_endpoint(self, model: str, stream: bool) -> tuple[str, dict[str, str]]:
        method = "streamGenerateContent" if stream else "generateContent"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:{method}"
        if stream:
            url += "?alt=sse"
        return url, {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}

    def _sa_endpoint(self, model: str, stream: bool, bearer: str) -> tuple[str, dict[str, str]]:
        method = "streamGenerateContent" if stream else "generateContent"
        url = (
            f"https://{self.location}-aiplatform.googleapis.com/v1/projects/"
            f"{self.project_id}/locations/{self.location}/publishers/google/models/{model}:{method}"
        )
        if stream:
            url += "?alt=sse"
        return url, {"Authorization": f"Bearer {bearer}", "Content-Type": "application/json"}

    async def _get_sa_bearer(self) -> str | None:
        """Fetch / cache an ADC access token for service-account mode."""
        now = time.time()
        if self._sa_token and now < self._sa_token_expiry - 60:
            return self._sa_token
        try:
            import google.auth  # type: ignore[import]
            import google.auth.transport.requests  # type: ignore[import]

            creds, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            req = google.auth.transport.requests.Request()
            creds.refresh(req)
            self._sa_token = creds.token
            self._sa_token_expiry = creds.expiry.timestamp() if creds.expiry else now + 3600
            return self._sa_token
        except Exception as e:
            log.warning(f"[vertex] ADC token fetch failed: {e}")
            return None

    async def _get_endpoint(
        self, model: str, stream: bool
    ) -> tuple[str, dict[str, str]] | tuple[None, None]:
        """Return (url, headers) for the configured auth mode."""
        if self.auth_type == "api_key":
            if not self.api_key:
                log.warning("[vertex] api_key auth_type but no vertex_api_key configured")
                return None, None
            return self._api_key_endpoint(model, stream)
        else:
            if not self.project_id:
                log.warning("[vertex] service_account auth_type but no vertex_project_id configured")
                return None, None
            bearer = await self._get_sa_bearer()
            if not bearer:
                return None, None
            return self._sa_endpoint(model, stream, bearer)

    # ------------------------------------------------------------------
    # Request builder
    # ------------------------------------------------------------------

    def _build_payload(
        self,
        request: ChatCompletionRequest,
        model: str,
        use_web_search: bool = False,
        fetched_images: dict[str, tuple[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Convert ChatCompletionRequest → Gemini API payload."""
        contents: list[dict] = []
        system_text: str | None = None

        for msg in request.messages:
            role = getattr(msg, "role", "") if not isinstance(msg, dict) else msg.get("role", "")
            content = (
                getattr(msg, "content", "") if not isinstance(msg, dict) else msg.get("content", "")
            ) or ""

            if role == "system":
                system_text = (system_text or "") + content + "\n"
                continue

            # ── tool result (OpenAI role="tool") → Gemini functionResponse ────────
            if role == "tool":
                tool_call_id = (
                    getattr(msg, "tool_call_id", None)
                    if not isinstance(msg, dict)
                    else msg.get("tool_call_id")
                ) or "unknown"
                fn_name = tool_call_id  # Gemini needs the function name; use tool_call_id as fallback
                # Try to recover the real function name from the prior assistant message
                for prev in reversed(contents):
                    if prev.get("role") == "model":
                        for p in prev.get("parts", []):
                            fc = p.get("functionCall", {})
                            if fc.get("name"):
                                fn_name = fc["name"]
                                break
                        break
                fn_resp_part = {
                    "functionResponse": {
                        "name": fn_name,
                        "response": {"output": content},
                    }
                }
                # Gemini requires functionResponse in a "user" turn
                if contents and contents[-1]["role"] == "user":
                    contents[-1]["parts"].append(fn_resp_part)
                else:
                    contents.append({"role": "user", "parts": [fn_resp_part]})
                continue

            # ── assistant message that contains tool_calls → functionCall parts ───
            tool_calls = (
                getattr(msg, "tool_calls", None)
                if not isinstance(msg, dict)
                else msg.get("tool_calls")
            )
            if role == "assistant" and tool_calls:
                parts = []
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        fn = tc.get("function", {})
                        fn_name = fn.get("name", "unknown")
                        try:
                            args = json.loads(fn.get("arguments", "{}"))
                        except (json.JSONDecodeError, TypeError):
                            args = {}
                    else:
                        fn = getattr(tc, "function", None)
                        fn_name = getattr(fn, "name", "unknown") if fn else "unknown"
                        try:
                            args = json.loads(getattr(fn, "arguments", "{}") or "{}")
                        except (json.JSONDecodeError, TypeError):
                            args = {}
                    parts.append({"functionCall": {"name": fn_name, "args": args}})
                if content:
                    parts.insert(0, {"text": content})
                contents.append({"role": "model", "parts": parts})
                continue

            # ── normal text / vision turn ─────────────────────────────────────────
            gemini_role = "model" if role == "assistant" else "user"

            # Build parts — include inline images when content_parts is present
            raw_parts = getattr(msg, "content_parts", None) if not isinstance(msg, dict) else None
            if raw_parts:
                parts = _openai_parts_to_gemini(raw_parts, fetched=fetched_images)
            else:
                parts = [{"text": content}] if content else [{"text": ""}]

            # Merge consecutive same-role turns (Gemini requires alternating)
            if contents and contents[-1]["role"] == gemini_role:
                contents[-1]["parts"].extend(parts)
            else:
                contents.append({"role": gemini_role, "parts": parts})

        payload: dict[str, Any] = {"contents": contents}

        if system_text:
            payload["systemInstruction"] = {
                "role": "user",
                "parts": [{"text": system_text.strip()}],
            }

        gen_config: dict[str, Any] = {}
        if request.max_tokens:
            gen_config["maxOutputTokens"] = request.max_tokens
        if request.temperature is not None:
            gen_config["temperature"] = request.temperature
        if gen_config:
            payload["generationConfig"] = gen_config

        # ── Function declarations (OpenAI tools → Gemini function_declarations) ──
        tools_payload: list[dict] = []
        if request.tools:
            fn_decls = []
            for t in request.tools:
                if isinstance(t, dict):
                    fn = t.get("function", {})
                else:
                    fn = getattr(t, "function", None)
                if fn is None:
                    continue
                fn_name = fn.get("name") if isinstance(fn, dict) else getattr(fn, "name", "")
                fn_desc = fn.get("description", "") if isinstance(fn, dict) else getattr(fn, "description", "")
                fn_params = fn.get("parameters", {}) if isinstance(fn, dict) else getattr(fn, "parameters", {})
                fn_decls.append({
                    "name": fn_name,
                    "description": fn_desc or "",
                    "parameters": fn_params or {"type": "object", "properties": {}},
                })
            if fn_decls:
                tools_payload.append({"function_declarations": fn_decls})

        # Gemini doesn't allow googleSearch + function_declarations in the same request
        if use_web_search and not request.tools:
            tools_payload.append({"googleSearch": {}})

        if tools_payload:
            payload["tools"] = tools_payload

        return payload

    # ------------------------------------------------------------------
    # Response normaliser
    # ------------------------------------------------------------------

    def _parse_response(
        self,
        data: dict,
        model: str,
        request_id: str,
    ) -> tuple[ChatCompletionResponse, GroundingMetadata | None]:
        """Parse a non-streaming Gemini response."""
        candidates = data.get("candidates", [])
        text = ""
        finish_reason = "stop"
        grounding: GroundingMetadata | None = None

        tool_calls_out: list[dict] | None = None

        if candidates:
            cand = candidates[0]
            parts = cand.get("content", {}).get("parts", [])
            raw_finish = cand.get("finishReason", "STOP")
            finish_reason = "stop" if raw_finish in ("STOP", "END_OF_TURN") else raw_finish.lower()

            # Separate text parts from functionCall parts
            text_parts = []
            fn_call_parts = []
            for p in parts:
                if "functionCall" in p:
                    fn_call_parts.append(p["functionCall"])
                elif "text" in p:
                    text_parts.append(p["text"])

            text = "".join(text_parts)

            # Convert Gemini functionCall → OpenAI tool_calls format
            if fn_call_parts:
                tool_calls_out = []
                for fc in fn_call_parts:
                    args = fc.get("args", {})
                    tool_calls_out.append({
                        "id": f"call_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {
                            "name": fc.get("name", "unknown"),
                            "arguments": json.dumps(args) if isinstance(args, dict) else (args or "{}"),
                        },
                    })
                finish_reason = "tool_calls"

            # Grounding metadata
            gm = cand.get("groundingMetadata")
            if gm:
                chunks = [
                    GroundingChunk(
                        uri=c.get("web", {}).get("uri", ""),
                        title=c.get("web", {}).get("title", ""),
                    )
                    for c in gm.get("groundingChunks", [])
                    if c.get("web")
                ]
                grounding = GroundingMetadata(
                    chunks=chunks,
                    web_search_queries=gm.get("webSearchQueries", []),
                )

        usage_meta = data.get("usageMetadata", {})
        usage = Usage(
            prompt_tokens=usage_meta.get("promptTokenCount", 0),
            completion_tokens=usage_meta.get("candidatesTokenCount", 0),
            total_tokens=usage_meta.get("totalTokenCount", 0),
        )

        response = ChatCompletionResponse(
            id=request_id,
            model=model,
            choices=[
                Choice(
                    message=ChoiceMessage(
                        role="assistant",
                        content=text or None,
                        tool_calls=tool_calls_out,
                    ),
                    finish_reason=finish_reason,
                )
            ],
            usage=usage,
            provider=self.name,
        )
        return response, grounding

    # ------------------------------------------------------------------
    # Completion (non-streaming)
    # ------------------------------------------------------------------

    async def complete(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        model = self._resolve_model(request.model)
        use_web_search = self.web_search_enabled or bool(
            getattr(request, "metadata", {}) and request.metadata.get("web_search")  # type: ignore[union-attr]
        )

        url, headers = await self._get_endpoint(model, stream=False)
        if url is None:
            raise RuntimeError("[vertex] No valid auth configuration — cannot dispatch request")

        fetched_images = await _fetch_image_urls(request.messages)
        payload = self._build_payload(request, model, use_web_search=use_web_search,
                                      fetched_images=fetched_images)
        request_id = f"vtx-{uuid.uuid4().hex[:12]}"

        resp = await self._client.post(url, headers=headers, json=payload)

        if resp.status_code != 200:
            body = resp.text
            err_type = _classify_error(resp.status_code, body)
            log.warning(
                f"[vertex] HTTP {resp.status_code} ({err_type}) model={model} body={body[:200]}"
            )
            raise RuntimeError(f"Vertex API error {resp.status_code}: {err_type} — {body[:200]}")

        data = resp.json()
        response, grounding = self._parse_response(data, model, request_id)

        if grounding and grounding.chunks:
            log.debug(
                f"[vertex] Grounding: {len(grounding.chunks)} sources, "
                f"queries={grounding.web_search_queries}"
            )
            # Attach grounding to response for downstream citation injection
            response.grounding_metadata = grounding.to_dict()  # type: ignore[attr-defined]

        return response

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    async def stream(self, request: ChatCompletionRequest) -> AsyncIterator[ChatCompletionChunk]:
        model = self._resolve_model(request.model)
        use_web_search = self.web_search_enabled or bool(
            getattr(request, "metadata", {}) and request.metadata.get("web_search")  # type: ignore[union-attr]
        )

        url, headers = await self._get_endpoint(model, stream=True)
        if url is None:
            raise RuntimeError("[vertex] No valid auth configuration — cannot stream")

        fetched_images = await _fetch_image_urls(request.messages)
        payload = self._build_payload(request, model, use_web_search=use_web_search,
                                      fetched_images=fetched_images)
        chunk_id = f"vtx-{uuid.uuid4().hex[:12]}"

        # Send role header chunk first
        yield ChatCompletionChunk(
            id=chunk_id,
            model=model,
            choices=[StreamChoice(delta=DeltaMessage(role="assistant"))],
        )

        grounding_accumulated: GroundingMetadata | None = None

        async with self._client.stream("POST", url, headers=headers, json=payload) as stream_resp:
            if stream_resp.status_code != 200:
                body = await stream_resp.aread()
                err_type = _classify_error(stream_resp.status_code, body.decode())
                raise RuntimeError(
                    f"Vertex stream error {stream_resp.status_code}: {err_type}"
                )

            async for line in stream_resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw in ("", "[DONE]"):
                    continue
                try:
                    chunk_data = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                candidates = chunk_data.get("candidates", [])
                if not candidates:
                    continue

                cand = candidates[0]
                parts = cand.get("content", {}).get("parts", [])
                text_delta = "".join(p.get("text", "") for p in parts)
                raw_finish = cand.get("finishReason")
                finish_reason: str | None = None
                if raw_finish and raw_finish not in ("", "FINISH_REASON_UNSPECIFIED", None):
                    finish_reason = "stop" if raw_finish in ("STOP", "END_OF_TURN") else raw_finish.lower()

                # Accumulate grounding
                gm = cand.get("groundingMetadata")
                if gm:
                    chunks = [
                        GroundingChunk(
                            uri=c.get("web", {}).get("uri", ""),
                            title=c.get("web", {}).get("title", ""),
                        )
                        for c in gm.get("groundingChunks", [])
                        if c.get("web")
                    ]
                    grounding_accumulated = GroundingMetadata(
                        chunks=chunks,
                        web_search_queries=gm.get("webSearchQueries", []),
                    )

                if text_delta or finish_reason:
                    yield ChatCompletionChunk(
                        id=chunk_id,
                        model=model,
                        choices=[
                            StreamChoice(
                                delta=DeltaMessage(content=text_delta if text_delta else None),
                                finish_reason=finish_reason,
                            )
                        ],
                    )

        # Final [DONE] sentinel
        yield ChatCompletionChunk(
            id=chunk_id,
            model=model,
            choices=[StreamChoice(delta=DeltaMessage(), finish_reason="stop")],
        )

        if grounding_accumulated and grounding_accumulated.chunks:
            log.debug(
                f"[vertex] Stream grounding: {len(grounding_accumulated.chunks)} sources"
            )

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Lightweight health check — list models endpoint (no token spend)."""
        if self.auth_type == "api_key":
            if not self.api_key:
                return False
            try:
                url = "https://generativelanguage.googleapis.com/v1beta/models"
                resp = await self._client.get(
                    url,
                    headers={"x-goog-api-key": self.api_key},
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    # Update our model list with what the API actually supports
                    try:
                        api_models = resp.json().get("models", [])
                        available = {
                            m["name"].replace("models/", "")
                            for m in api_models
                            if "generateContent" in m.get("supportedGenerationMethods", [])
                        }
                        # Mark which of our configured models are actually available
                        for m in self._models:
                            m._available = m.name in available  # type: ignore[attr-defined]
                    except Exception:
                        pass
                    return True
                return False
            except Exception:
                return False
        else:
            if not self.project_id:
                return False
            try:
                bearer = await self._get_sa_bearer()
                if not bearer:
                    return False
                url = (
                    f"https://{self.location}-aiplatform.googleapis.com/v1/projects/"
                    f"{self.project_id}/locations/{self.location}/publishers/google/models"
                )
                resp = await self._client.get(
                    url,
                    headers={"Authorization": f"Bearer {bearer}"},
                    timeout=10.0,
                )
                return resp.status_code == 200
            except Exception:
                return False
