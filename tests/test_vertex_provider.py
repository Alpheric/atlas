"""Tests for the Vertex AI / Gemini provider.

Tests cover:
- Model alias resolution
- Request payload building (system message extraction, role mapping, grounding tool)
- Non-streaming response parsing (text, usage, grounding metadata)
- Streaming response chunk parsing
- PII masking is applied before dispatch (mocked at CorePipeline boundary)
- Fallback when auth is not configured
- Health check in both auth modes
- Error normalization
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_provider(auth_type="api_key", api_key="test-key", project_id="", web_search=False):
    """Construct a VertexProvider with injected settings."""
    with (
        patch("config.settings.settings") as mock_settings,
        patch(
            "a1.providers.vertex.settings",
            vertex_auth_type=auth_type,
            vertex_api_key=api_key,
            vertex_project_id=project_id,
            vertex_location="us-central1",
            vertex_default_model="gemini-2.0-flash",
            vertex_web_search_enabled=web_search,
            vertex_timeout=60.0,
        ),
    ):
        from a1.providers.vertex import VertexProvider

        return VertexProvider()


def _make_request(model="gemini-2.0-flash", messages=None, max_tokens=None, temperature=None):
    """Build a minimal ChatCompletionRequest."""
    from a1.proxy.request_models import ChatCompletionRequest, MessageInput

    if messages is None:
        messages = [MessageInput(role="user", content="Hello")]

    return ChatCompletionRequest(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )


# ---------------------------------------------------------------------------
# Model alias resolution
# ---------------------------------------------------------------------------


class TestModelAliases:
    def test_vertex_gemini_pro_alias(self):
        from a1.providers.vertex import _ALIASES

        assert _ALIASES["vertex_gemini_pro"] == "gemini-1.5-pro"

    def test_vertex_gemini_flash_alias(self):
        from a1.providers.vertex import _ALIASES

        assert _ALIASES["vertex_gemini_flash"] == "gemini-2.0-flash"

    def test_unknown_model_passes_through(self):
        from a1.providers.vertex import _ALIASES

        assert _ALIASES.get("gemini-2.0-flash", "gemini-2.0-flash") == "gemini-2.0-flash"

    def test_provider_supports_alias(self):
        from a1.providers.vertex import VertexProvider

        with patch(
            "a1.providers.vertex.settings",
            vertex_auth_type="api_key",
            vertex_api_key="k",
            vertex_project_id="",
            vertex_location="us-central1",
            vertex_default_model="gemini-2.0-flash",
            vertex_web_search_enabled=False,
            vertex_timeout=60.0,
        ):
            provider = VertexProvider()
        assert provider.supports_model("vertex_gemini_flash")
        assert provider.supports_model("gemini-2.0-flash")
        assert not provider.supports_model("gpt-4o")


# ---------------------------------------------------------------------------
# Payload building
# ---------------------------------------------------------------------------


class TestPayloadBuilding:
    def _provider(self):
        from a1.providers.vertex import VertexProvider

        with patch(
            "a1.providers.vertex.settings",
            vertex_auth_type="api_key",
            vertex_api_key="k",
            vertex_project_id="",
            vertex_location="us-central1",
            vertex_default_model="gemini-2.0-flash",
            vertex_web_search_enabled=False,
            vertex_timeout=60.0,
        ):
            return VertexProvider()

    def test_system_message_extracted(self):
        from a1.proxy.request_models import MessageInput

        prov = self._provider()
        request = _make_request(
            messages=[
                MessageInput(role="system", content="You are Atlas."),
                MessageInput(role="user", content="Hello"),
            ]
        )
        payload = prov._build_payload(request, "gemini-2.0-flash")
        assert "systemInstruction" in payload
        assert "Atlas" in payload["systemInstruction"]["parts"][0]["text"]
        # System message should NOT appear in contents
        for c in payload["contents"]:
            assert c["role"] != "system"

    def test_user_and_assistant_roles_mapped(self):
        from a1.proxy.request_models import MessageInput

        prov = self._provider()
        request = _make_request(
            messages=[
                MessageInput(role="user", content="Hi"),
                MessageInput(role="assistant", content="Hello!"),
                MessageInput(role="user", content="How are you?"),
            ]
        )
        payload = prov._build_payload(request, "gemini-2.0-flash")
        roles = [c["role"] for c in payload["contents"]]
        assert roles == ["user", "model", "user"]

    def test_consecutive_same_roles_merged(self):
        """Gemini requires alternating roles — consecutive user messages should merge."""
        from a1.proxy.request_models import MessageInput

        prov = self._provider()
        request = _make_request(
            messages=[
                MessageInput(role="user", content="First"),
                MessageInput(role="user", content="Second"),
            ]
        )
        payload = prov._build_payload(request, "gemini-2.0-flash")
        assert len(payload["contents"]) == 1
        texts = [p["text"] for p in payload["contents"][0]["parts"]]
        assert "First" in texts
        assert "Second" in texts

    def test_web_search_tool_added_when_enabled(self):
        prov = self._provider()
        request = _make_request()
        payload = prov._build_payload(request, "gemini-2.0-flash", use_web_search=True)
        assert "tools" in payload
        assert any("googleSearch" in t for t in payload["tools"])

    def test_web_search_tool_absent_when_disabled(self):
        prov = self._provider()
        request = _make_request()
        payload = prov._build_payload(request, "gemini-2.0-flash", use_web_search=False)
        assert "tools" not in payload

    def test_generation_config_set(self):
        prov = self._provider()
        request = _make_request(max_tokens=512, temperature=0.5)
        payload = prov._build_payload(request, "gemini-2.0-flash")
        assert payload["generationConfig"]["maxOutputTokens"] == 512
        assert payload["generationConfig"]["temperature"] == 0.5


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


class TestResponseParsing:
    def _provider(self):
        from a1.providers.vertex import VertexProvider

        with patch(
            "a1.providers.vertex.settings",
            vertex_auth_type="api_key",
            vertex_api_key="k",
            vertex_project_id="",
            vertex_location="us-central1",
            vertex_default_model="gemini-2.0-flash",
            vertex_web_search_enabled=False,
            vertex_timeout=60.0,
        ):
            return VertexProvider()

    def _gemini_response(self, text="Hello!", finish="STOP", grounding=None):
        cand = {
            "content": {"parts": [{"text": text}], "role": "model"},
            "finishReason": finish,
        }
        if grounding:
            cand["groundingMetadata"] = grounding
        return {
            "candidates": [cand],
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 5,
                "totalTokenCount": 15,
            },
        }

    def test_text_extracted(self):
        prov = self._provider()
        data = self._gemini_response("The answer is 42.")
        response, _ = prov._parse_response(data, "gemini-2.0-flash", "req-1")
        assert response.choices[0].message.content == "The answer is 42."

    def test_usage_populated(self):
        prov = self._provider()
        data = self._gemini_response()
        response, _ = prov._parse_response(data, "gemini-2.0-flash", "req-1")
        assert response.usage.prompt_tokens == 10
        assert response.usage.completion_tokens == 5
        assert response.usage.total_tokens == 15

    def test_finish_reason_normalised(self):
        prov = self._provider()
        data = self._gemini_response(finish="STOP")
        response, _ = prov._parse_response(data, "gemini-2.0-flash", "req-1")
        assert response.choices[0].finish_reason == "stop"

    def test_grounding_metadata_parsed(self):
        prov = self._provider()
        grounding = {
            "groundingChunks": [
                {"web": {"uri": "https://example.com", "title": "Example"}},
                {"web": {"uri": "https://another.com", "title": "Another"}},
            ],
            "webSearchQueries": ["test query"],
        }
        data = self._gemini_response("Grounded answer.", grounding=grounding)
        _, gm = prov._parse_response(data, "gemini-2.0-flash", "req-1")
        assert gm is not None
        assert len(gm.chunks) == 2
        assert gm.chunks[0].uri == "https://example.com"
        assert gm.web_search_queries == ["test query"]

    def test_no_grounding_returns_none(self):
        prov = self._provider()
        data = self._gemini_response()
        _, gm = prov._parse_response(data, "gemini-2.0-flash", "req-1")
        assert gm is None

    def test_empty_candidates_handled(self):
        prov = self._provider()
        data = {"candidates": [], "usageMetadata": {}}
        response, _ = prov._parse_response(data, "gemini-2.0-flash", "req-1")
        assert response.choices[0].message.content == ""


# ---------------------------------------------------------------------------
# Error normalisation
# ---------------------------------------------------------------------------


class TestErrorNormalisation:
    def test_quota_exceeded(self):
        from a1.providers.vertex import _classify_error

        assert _classify_error(429, "Quota exceeded for model") == "quota_exceeded"

    def test_rate_limited(self):
        from a1.providers.vertex import _classify_error

        assert _classify_error(429, "Too many requests") == "rate_limited"

    def test_permission_denied(self):
        from a1.providers.vertex import _classify_error

        assert _classify_error(403, "Permission denied") == "permission_denied"

    def test_model_not_found(self):
        from a1.providers.vertex import _classify_error

        assert _classify_error(404, "Model not found") == "model_not_found"

    def test_authentication_error(self):
        from a1.providers.vertex import _classify_error

        assert _classify_error(401, "Invalid API key") == "authentication_error"


# ---------------------------------------------------------------------------
# complete() integration (mocked HTTP)
# ---------------------------------------------------------------------------


class TestComplete:
    def _provider_with_key(self):
        from a1.providers.vertex import VertexProvider

        with patch(
            "a1.providers.vertex.settings",
            vertex_auth_type="api_key",
            vertex_api_key="test-key-123",
            vertex_project_id="",
            vertex_location="us-central1",
            vertex_default_model="gemini-2.0-flash",
            vertex_web_search_enabled=False,
            vertex_timeout=60.0,
        ):
            return VertexProvider()

    @pytest.mark.asyncio
    async def test_successful_completion(self):
        prov = self._provider_with_key()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "candidates": [
                {
                    "content": {"parts": [{"text": "Paris"}], "role": "model"},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 8,
                "candidatesTokenCount": 1,
                "totalTokenCount": 9,
            },
        }

        prov._client = AsyncMock()
        prov._client.post = AsyncMock(return_value=mock_resp)

        request = _make_request()
        response = await prov.complete(request)

        assert response.choices[0].message.content == "Paris"
        assert response.usage.total_tokens == 9
        assert response.provider == "vertex"

    @pytest.mark.asyncio
    async def test_complete_raises_on_http_error(self):
        prov = self._provider_with_key()
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.text = "Quota exceeded"

        prov._client = AsyncMock()
        prov._client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(RuntimeError, match="quota_exceeded"):
            await prov.complete(_make_request())

    @pytest.mark.asyncio
    async def test_complete_no_auth_raises(self):
        from a1.providers.vertex import VertexProvider

        with patch(
            "a1.providers.vertex.settings",
            vertex_auth_type="api_key",
            vertex_api_key="",  # no key
            vertex_project_id="",
            vertex_location="us-central1",
            vertex_default_model="gemini-2.0-flash",
            vertex_web_search_enabled=False,
            vertex_timeout=60.0,
        ):
            prov = VertexProvider()

        with pytest.raises(RuntimeError, match="No valid auth"):
            await prov.complete(_make_request())

    @pytest.mark.asyncio
    async def test_complete_attaches_grounding(self):
        prov = self._provider_with_key()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "candidates": [
                {
                    "content": {"parts": [{"text": "Answer"}], "role": "model"},
                    "finishReason": "STOP",
                    "groundingMetadata": {
                        "groundingChunks": [
                            {"web": {"uri": "https://example.com", "title": "Ex"}}
                        ],
                        "webSearchQueries": ["query"],
                    },
                }
            ],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 2, "totalTokenCount": 7},
        }

        prov._client = AsyncMock()
        prov._client.post = AsyncMock(return_value=mock_resp)

        response = await prov.complete(_make_request())
        # grounding_metadata attached as extra attr
        assert hasattr(response, "grounding_metadata")
        gm = response.grounding_metadata
        assert len(gm["chunks"]) == 1
        assert gm["chunks"][0]["uri"] == "https://example.com"


# ---------------------------------------------------------------------------
# stream() integration (mocked SSE)
# ---------------------------------------------------------------------------


class TestStream:
    def _provider_with_key(self):
        from a1.providers.vertex import VertexProvider

        with patch(
            "a1.providers.vertex.settings",
            vertex_auth_type="api_key",
            vertex_api_key="test-key-123",
            vertex_project_id="",
            vertex_location="us-central1",
            vertex_default_model="gemini-2.0-flash",
            vertex_web_search_enabled=False,
            vertex_timeout=60.0,
        ):
            return VertexProvider()

    def _make_sse_lines(self, chunks: list[str]) -> list[str]:
        lines = []
        for text in chunks:
            data = {
                "candidates": [
                    {
                        "content": {"parts": [{"text": text}], "role": "model"},
                        "finishReason": "",
                    }
                ]
            }
            lines.append(f"data: {json.dumps(data)}")
        # Final chunk with STOP
        final = {
            "candidates": [
                {
                    "content": {"parts": [{"text": ""}], "role": "model"},
                    "finishReason": "STOP",
                }
            ]
        }
        lines.append(f"data: {json.dumps(final)}")
        return lines

    @pytest.mark.asyncio
    async def test_stream_yields_role_then_content(self):
        prov = self._provider_with_key()
        sse_lines = self._make_sse_lines(["Hello", " World"])

        # Build a proper async context manager that supports aiter_lines() as async iterator
        class MockStreamResp:
            status_code = 200

            async def aread(self):
                return b""

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def aiter_lines(self):
                for line in sse_lines:
                    yield line

        prov._client = MagicMock()
        prov._client.stream = MagicMock(return_value=MockStreamResp())

        chunks = []
        async for c in prov.stream(_make_request()):
            chunks.append(c)

        # First chunk: role header
        assert chunks[0].choices[0].delta.role == "assistant"
        # Subsequent chunks contain text
        texts = [c.choices[0].delta.content for c in chunks[1:] if c.choices[0].delta.content]
        assert "Hello" in texts
        assert " World" in texts

    @pytest.mark.asyncio
    async def test_stream_raises_on_error_status(self):
        prov = self._provider_with_key()

        class MockErrorStreamResp:
            status_code = 403

            async def aread(self):
                return b"Permission denied"

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def aiter_lines(self):
                return
                yield  # make it a generator

        prov._client = MagicMock()
        prov._client.stream = MagicMock(return_value=MockErrorStreamResp())

        with pytest.raises(RuntimeError, match="permission_denied"):
            async for _ in prov.stream(_make_request()):
                pass


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


class TestHealthCheck:
    def _provider_api_key(self):
        from a1.providers.vertex import VertexProvider

        with patch(
            "a1.providers.vertex.settings",
            vertex_auth_type="api_key",
            vertex_api_key="test-key",
            vertex_project_id="",
            vertex_location="us-central1",
            vertex_default_model="gemini-2.0-flash",
            vertex_web_search_enabled=False,
            vertex_timeout=60.0,
        ):
            return VertexProvider()

    @pytest.mark.asyncio
    async def test_health_check_true_on_200(self):
        prov = self._provider_api_key()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        prov._client = AsyncMock()
        prov._client.get = AsyncMock(return_value=mock_resp)
        assert await prov.health_check() is True

    @pytest.mark.asyncio
    async def test_health_check_false_on_401(self):
        prov = self._provider_api_key()
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        prov._client = AsyncMock()
        prov._client.get = AsyncMock(return_value=mock_resp)
        assert await prov.health_check() is False

    @pytest.mark.asyncio
    async def test_health_check_false_when_no_key(self):
        from a1.providers.vertex import VertexProvider

        with patch(
            "a1.providers.vertex.settings",
            vertex_auth_type="api_key",
            vertex_api_key="",
            vertex_project_id="",
            vertex_location="us-central1",
            vertex_default_model="gemini-2.0-flash",
            vertex_web_search_enabled=False,
            vertex_timeout=60.0,
        ):
            prov = VertexProvider()
        assert await prov.health_check() is False

    @pytest.mark.asyncio
    async def test_health_check_false_on_exception(self):
        prov = self._provider_api_key()
        prov._client = AsyncMock()
        prov._client.get = AsyncMock(side_effect=Exception("network error"))
        assert await prov.health_check() is False


# ---------------------------------------------------------------------------
# PII masking boundary test
# (PII is masked by CorePipeline before calling provider — verify content)
# ---------------------------------------------------------------------------


class TestPIIBoundary:
    @pytest.mark.asyncio
    async def test_masked_content_sent_to_api(self):
        """Verify that the payload sent to Vertex contains the (pre-masked) content
        from the request — i.e. the provider does not unmask or re-introduce PII."""
        from a1.providers.vertex import VertexProvider

        with patch(
            "a1.providers.vertex.settings",
            vertex_auth_type="api_key",
            vertex_api_key="k",
            vertex_project_id="",
            vertex_location="us-central1",
            vertex_default_model="gemini-2.0-flash",
            vertex_web_search_enabled=False,
            vertex_timeout=60.0,
        ):
            prov = VertexProvider()

        captured_payload = {}

        async def mock_post(url, headers, json):
            captured_payload.update(json)
            mock_r = MagicMock()
            mock_r.status_code = 200
            mock_r.json.return_value = {
                "candidates": [
                    {"content": {"parts": [{"text": "ok"}], "role": "model"}, "finishReason": "STOP"}
                ],
                "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2},
            }
            return mock_r

        prov._client = AsyncMock()
        prov._client.post = mock_post

        from a1.proxy.request_models import ChatCompletionRequest, MessageInput

        # Simulates PII-masked content (email replaced)
        request = ChatCompletionRequest(
            model="gemini-2.0-flash",
            messages=[MessageInput(role="user", content="Email: [EMAIL_0]")],
        )
        await prov.complete(request)

        sent_text = captured_payload["contents"][0]["parts"][0]["text"]
        assert "[EMAIL_0]" in sent_text
        assert "@" not in sent_text  # actual email not re-introduced


# ---------------------------------------------------------------------------
# Async helper
# ---------------------------------------------------------------------------


async def aiter(iterable):
    for item in iterable:
        yield item
