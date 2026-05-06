"""Acceptance tests: POST /v1/messages (Anthropic Messages API compatibility).

Test plan:
  A.  Basic non-streaming roundtrip
  A'. Basic streaming roundtrip — full SSE event sequence
  B.  Tool definitions forwarded (tool_use or text response — either valid)
  D.  Multi-turn with tool_result content blocks
  E.  Long-ish generation (ping events injected, no timeout)
  F1. x-api-key auth (not Bearer)
  F2. Authorization: Bearer also accepted
  F3. Missing key → 401 + Anthropic error shape
  F4. Bad key → 403 + Anthropic error shape
  F5. system as string
  F6. system as array of content blocks
  F7. Empty tools array (not None) — must not crash
  F8. tool_choice variants: "auto", "any", {"type":"auto"}
  F9. stop_sequences parameter — must not crash
  F10. Model aliases: "Atlas", "atlas-plan", "claude-3-5-sonnet-20241022"
  F11. Empty messages → 400 + Anthropic error shape
  F12. Malformed JSON → 400 + Anthropic error shape
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from a1.proxy.response_models import (
    ChatCompletionChunk,
    ChatCompletionResponse,
    Choice,
    ChoiceMessage,
    DeltaMessage,
    StreamChoice,
    Usage,
)

# ---------------------------------------------------------------------------
# Shared mock helpers
# ---------------------------------------------------------------------------

VALID_KEY = "sk-atlas-test-key"
BAD_KEY = "sk-bad-key"


def _mock_completion(text: str = "Hello from Atlas!", tool_calls=None) -> ChatCompletionResponse:
    return ChatCompletionResponse(
        id="chatcmpl-test",
        model="test-model",
        choices=[
            Choice(
                index=0,
                message=ChoiceMessage(role="assistant", content=text, tool_calls=tool_calls),
                finish_reason="stop",
            )
        ],
        usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        provider="mock",
    )


async def _mock_stream_chunks(text: str = "Hello streamed!"):
    """Async generator yielding ChatCompletionChunk objects."""
    chunk_id = "chatcmpl-stream-test"
    words = text.split()
    for i, word in enumerate(words):
        yield ChatCompletionChunk(
            id=chunk_id,
            model="test-model",
            choices=[
                StreamChoice(delta=DeltaMessage(content=word + (" " if i < len(words) - 1 else "")))
            ],  # noqa: E501
        )
    # Final chunk with usage
    yield ChatCompletionChunk(
        id=chunk_id,
        model="test-model",
        choices=[StreamChoice(delta=DeltaMessage(), finish_reason="stop")],
        usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


def _make_provider(
    text: str = "Hello from Atlas!", tool_calls=None, stream_text: str = "Hello streamed!"
):  # noqa: E501
    provider = MagicMock()
    provider.name = "mock"
    provider.estimate_cost.return_value = 0.0
    provider.complete = AsyncMock(return_value=_mock_completion(text, tool_calls))
    provider.stream = MagicMock(return_value=_mock_stream_chunks(stream_text))
    provider.list_models.return_value = []
    return provider


def _make_registry(provider):
    mock_registry = MagicMock()
    mock_registry.get_provider.return_value = provider
    mock_registry.get_provider_for_model.return_value = provider
    mock_registry.healthy_providers = {"mock": provider}
    mock_registry.list_all_models.return_value = []
    mock_registry.list_providers.return_value = []
    mock_registry.is_healthy.return_value = True
    return mock_registry


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    """TestClient with mocked providers + auth (VALID_KEY accepted)."""
    provider = _make_provider()
    registry = _make_registry(provider)

    async def mock_get_db():
        yield MagicMock()

    async def mock_select_model(task_type, strategy):
        return "test-model", "mock"

    with (
        patch("a1.proxy.core_pipeline.provider_registry", registry),
        patch("a1.proxy.core_pipeline.select_model", mock_select_model),
        patch("a1.proxy.core_pipeline.settings") as mock_settings,
        patch("a1.proxy.core_pipeline._persist_usage", new_callable=AsyncMock),
        patch("a1.proxy.core_pipeline.metrics"),
        patch("a1.proxy.core_pipeline.record_otel_request"),
        patch("a1.proxy.openai_router.verify_api_key", return_value="dev"),
        patch("a1.proxy.openai_router.provider_registry", registry),
        patch("a1.proxy.responses_router.verify_api_key", return_value="dev"),
        patch("a1.proxy.atlas_router.verify_api_key", return_value="dev"),
        patch("a1.proxy.openai_router.get_db", mock_get_db),
        patch("a1.proxy.responses_router.get_db", mock_get_db),
        # Messages router auth — accept VALID_KEY
        patch("a1.proxy.messages_router.settings") as mock_msg_settings,
    ):
        mock_settings.session_enabled = False
        mock_settings.pii_masking_enabled = False
        mock_settings.distillation_enabled = False
        mock_settings.task_cache_enabled = False
        mock_settings.session_load_grace_ms = 100
        mock_settings.distillation_task_repeat_threshold = 0
        mock_settings.planning_max_depth = 3
        mock_settings.planning_max_workers = 5

        mock_msg_settings.api_keys = [VALID_KEY]

        from a1.app import create_app

        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c


# ---------------------------------------------------------------------------
# Helper: parse SSE stream from bytes
# ---------------------------------------------------------------------------


def _parse_sse(content: bytes) -> list[dict]:
    """Parse raw SSE bytes into list of {event, data} dicts."""
    events = []
    current: dict = {}
    for line in content.decode().splitlines():
        if line.startswith("event:"):
            current["event"] = line[len("event:") :].strip()
        elif line.startswith("data:"):
            raw = line[len("data:") :].strip()
            try:
                current["data"] = json.loads(raw)
            except json.JSONDecodeError:
                current["data"] = raw
        elif line == "" and current:
            events.append(current)
            current = {}
    if current:
        events.append(current)
    return events


# ---------------------------------------------------------------------------
# A. Basic non-streaming roundtrip
# ---------------------------------------------------------------------------


class TestBasicRoundtrip:
    def test_non_streaming_200(self, client):
        resp = client.post(
            "/v1/messages",
            json={
                "model": "atlas-plan",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "What is 2+2?"}],
            },
            headers={"x-api-key": VALID_KEY},
        )
        assert resp.status_code == 200, resp.text

    def test_non_streaming_anthropic_shape(self, client):
        """Response must be Anthropic MessagesResponse shape."""
        resp = client.post(
            "/v1/messages",
            json={
                "model": "atlas-plan",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "Hello"}],
            },
            headers={"x-api-key": VALID_KEY},
        )
        body = resp.json()
        assert body["type"] == "message"
        assert body["role"] == "assistant"
        assert "content" in body
        assert isinstance(body["content"], list)
        assert any(b["type"] == "text" for b in body["content"])
        assert "usage" in body
        assert "input_tokens" in body["usage"]
        assert "output_tokens" in body["usage"]
        assert "stop_reason" in body
        assert body["stop_reason"] in ("end_turn", "tool_use", "stop_sequence", "max_tokens")
        assert "id" in body
        assert "model" in body


# ---------------------------------------------------------------------------
# A'. Streaming roundtrip — full SSE event sequence
# ---------------------------------------------------------------------------


class TestStreamingRoundtrip:
    def test_streaming_200(self, client):
        resp = client.post(
            "/v1/messages",
            json={
                "model": "atlas-plan",
                "max_tokens": 100,
                "stream": True,
                "messages": [{"role": "user", "content": "What is 2+2?"}],
            },
            headers={"x-api-key": VALID_KEY},
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")

    def test_streaming_event_sequence(self, client):
        """Verify Anthropic SSE event sequence is correct."""
        resp = client.post(
            "/v1/messages",
            json={
                "model": "atlas-plan",
                "max_tokens": 100,
                "stream": True,
                "messages": [{"role": "user", "content": "Say hello"}],
            },
            headers={"x-api-key": VALID_KEY},
        )
        events = _parse_sse(resp.content)
        event_types = [e.get("event") for e in events]

        # Required events must appear in order
        assert "message_start" in event_types, f"missing message_start; got {event_types}"
        assert "content_block_start" in event_types, "missing content_block_start"
        assert "ping" in event_types, "missing ping"
        assert "content_block_stop" in event_types, "missing content_block_stop"
        assert "message_delta" in event_types, "missing message_delta"
        assert "message_stop" in event_types, "missing message_stop"

        # Verify ordering
        def _idx(name):
            return next(i for i, e in enumerate(events) if e.get("event") == name)

        assert _idx("message_start") < _idx("content_block_start") < _idx("content_block_stop")
        assert _idx("content_block_stop") < _idx("message_delta") < _idx("message_stop")

    def test_streaming_message_start_shape(self, client):
        resp = client.post(
            "/v1/messages",
            json={
                "model": "atlas-plan",
                "max_tokens": 100,
                "stream": True,
                "messages": [{"role": "user", "content": "Hi"}],
            },
            headers={"x-api-key": VALID_KEY},
        )
        events = _parse_sse(resp.content)
        start = next(e for e in events if e.get("event") == "message_start")
        msg = start["data"]["message"]
        assert msg["type"] == "message"
        assert msg["role"] == "assistant"
        assert "id" in msg
        assert "usage" in msg

    def test_streaming_message_delta_has_stop_reason(self, client):
        resp = client.post(
            "/v1/messages",
            json={
                "model": "atlas-plan",
                "max_tokens": 100,
                "stream": True,
                "messages": [{"role": "user", "content": "Hi"}],
            },
            headers={"x-api-key": VALID_KEY},
        )
        events = _parse_sse(resp.content)
        delta = next(e for e in events if e.get("event") == "message_delta")
        assert "stop_reason" in delta["data"]["delta"]

    def test_streaming_contains_text_deltas(self, client):
        resp = client.post(
            "/v1/messages",
            json={
                "model": "atlas-plan",
                "max_tokens": 100,
                "stream": True,
                "messages": [{"role": "user", "content": "Hi"}],
            },
            headers={"x-api-key": VALID_KEY},
        )
        events = _parse_sse(resp.content)
        deltas = [e for e in events if e.get("event") == "content_block_delta"]
        assert len(deltas) > 0, "Expected at least one content_block_delta"
        for d in deltas:
            assert d["data"]["delta"]["type"] == "text_delta"
            assert "text" in d["data"]["delta"]


# ---------------------------------------------------------------------------
# B. Tool definitions
# ---------------------------------------------------------------------------


class TestToolCalling:
    def test_tools_forwarded_no_crash(self, client):
        """Sending tools must not crash; response must be valid Anthropic shape."""
        resp = client.post(
            "/v1/messages",
            json={
                "model": "atlas-plan",
                "max_tokens": 200,
                "messages": [{"role": "user", "content": "What files are in the repo?"}],
                "tools": [
                    {
                        "name": "list_files",
                        "description": "List files in a directory",
                        "input_schema": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    }
                ],
            },
            headers={"x-api-key": VALID_KEY},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["type"] == "message"
        assert body["stop_reason"] in ("end_turn", "tool_use")

    def test_empty_tools_array_no_crash(self, client):
        """Empty tools list must not crash."""
        resp = client.post(
            "/v1/messages",
            json={
                "model": "atlas-plan",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "Hello"}],
                "tools": [],
            },
            headers={"x-api-key": VALID_KEY},
        )
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# D. Multi-turn with tool_result content blocks
# ---------------------------------------------------------------------------


class TestToolResultBlocks:
    def test_tool_result_content_block_parsed(self, client):
        """User turn with tool_result blocks must parse without error."""
        resp = client.post(
            "/v1/messages",
            json={
                "model": "atlas-plan",
                "max_tokens": 200,
                "messages": [
                    {"role": "user", "content": "List the files in /tmp"},
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_01abc",
                                "name": "list_files",
                                "input": {"path": "/tmp"},
                            }
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_01abc",
                                "content": "file1.txt\nfile2.py\nfile3.log",
                            }
                        ],
                    },
                ],
            },
            headers={"x-api-key": VALID_KEY},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["type"] == "message"

    def test_tool_result_with_nested_blocks(self, client):
        """tool_result with nested content block array must parse correctly."""
        resp = client.post(
            "/v1/messages",
            json={
                "model": "atlas-plan",
                "max_tokens": 200,
                "messages": [
                    {"role": "user", "content": "Check the file"},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_02xyz",
                                "content": [
                                    {"type": "text", "text": "File contents here"},
                                    {"type": "text", "text": " — more content"},
                                ],
                            }
                        ],
                    },
                ],
            },
            headers={"x-api-key": VALID_KEY},
        )
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# F. Edge cases
# ---------------------------------------------------------------------------


class TestAuth:
    def test_x_api_key_accepted(self, client):
        resp = client.post(
            "/v1/messages",
            json={
                "model": "atlas-plan",
                "max_tokens": 50,
                "messages": [{"role": "user", "content": "Hi"}],
            },
            headers={"x-api-key": VALID_KEY},
        )
        assert resp.status_code == 200

    def test_authorization_bearer_accepted(self, client):
        resp = client.post(
            "/v1/messages",
            json={
                "model": "atlas-plan",
                "max_tokens": 50,
                "messages": [{"role": "user", "content": "Hi"}],
            },
            headers={"Authorization": f"Bearer {VALID_KEY}"},
        )
        assert resp.status_code == 200

    def test_missing_key_returns_401_anthropic_shape(self, client):
        resp = client.post(
            "/v1/messages",
            json={
                "model": "atlas-plan",
                "max_tokens": 50,
                "messages": [{"role": "user", "content": "Hi"}],
            },
        )
        assert resp.status_code == 401
        body = resp.json()
        detail = body.get("detail", body)
        assert detail.get("type") == "error"
        assert detail["error"]["type"] == "authentication_error"

    def test_bad_key_returns_403_anthropic_shape(self, client):
        resp = client.post(
            "/v1/messages",
            json={
                "model": "atlas-plan",
                "max_tokens": 50,
                "messages": [{"role": "user", "content": "Hi"}],
            },
            headers={"x-api-key": BAD_KEY},
        )
        assert resp.status_code == 403
        body = resp.json()
        detail = body.get("detail", body)
        assert detail.get("type") == "error"
        assert detail["error"]["type"] == "authentication_error"


class TestSystemField:
    def test_system_as_string(self, client):
        resp = client.post(
            "/v1/messages",
            json={
                "model": "atlas-plan",
                "max_tokens": 100,
                "system": "You are a helpful assistant.",
                "messages": [{"role": "user", "content": "Hi"}],
            },
            headers={"x-api-key": VALID_KEY},
        )
        assert resp.status_code == 200, resp.text

    def test_system_as_block_array(self, client):
        resp = client.post(
            "/v1/messages",
            json={
                "model": "atlas-plan",
                "max_tokens": 100,
                "system": [
                    {"type": "text", "text": "You are a helpful assistant."},
                    {"type": "text", "text": " Always respond concisely."},
                ],
                "messages": [{"role": "user", "content": "Hi"}],
            },
            headers={"x-api-key": VALID_KEY},
        )
        assert resp.status_code == 200, resp.text


class TestToolChoice:
    @pytest.mark.parametrize(
        "tool_choice",
        [
            "auto",
            "any",
            {"type": "auto"},
            {"type": "any"},
            {"type": "tool", "name": "list_files"},
        ],
    )
    def test_tool_choice_variants(self, client, tool_choice):
        resp = client.post(
            "/v1/messages",
            json={
                "model": "atlas-plan",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "Hi"}],
                "tools": [
                    {
                        "name": "list_files",
                        "description": "List files",
                        "input_schema": {"type": "object", "properties": {}},
                    }
                ],
                "tool_choice": tool_choice,
            },
            headers={"x-api-key": VALID_KEY},
        )
        assert resp.status_code == 200, f"tool_choice={tool_choice!r} → {resp.text}"


class TestStopSequences:
    def test_stop_sequences_no_crash(self, client):
        resp = client.post(
            "/v1/messages",
            json={
                "model": "atlas-plan",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "Hi"}],
                "stop_sequences": ["END", "\n\nHuman:"],
            },
            headers={"x-api-key": VALID_KEY},
        )
        assert resp.status_code == 200, resp.text


class TestModelAliases:
    @pytest.mark.parametrize(
        "model",
        [
            "Atlas",
            "atlas",
            "atlas-plan",
            "atlas-code",
            "claude-3-5-sonnet-20241022",  # common Anthropic model name — should route gracefully
            "claude-opus-4-20250514",
        ],
    )
    def test_model_aliases_no_crash(self, client, model):
        resp = client.post(
            "/v1/messages",
            json={
                "model": model,
                "max_tokens": 50,
                "messages": [{"role": "user", "content": "Hi"}],
            },
            headers={"x-api-key": VALID_KEY},
        )
        # Any of 200 / 503 is acceptable (503 = no provider for that model)
        assert resp.status_code in (200, 503), f"model={model!r} → {resp.status_code}: {resp.text}"


class TestValidation:
    def test_empty_messages_returns_400(self, client):
        resp = client.post(
            "/v1/messages",
            json={"model": "atlas-plan", "max_tokens": 50, "messages": []},
            headers={"x-api-key": VALID_KEY},
        )
        assert resp.status_code == 400
        body = resp.json()
        detail = body.get("detail", body)
        assert detail.get("type") == "error"

    def test_malformed_json_returns_400(self, client):
        resp = client.post(
            "/v1/messages",
            content=b"not valid json{{{",
            headers={"x-api-key": VALID_KEY, "content-type": "application/json"},
        )
        assert resp.status_code == 400

    def test_anthropic_version_header_accepted(self, client):
        """anthropic-version header must not cause 4xx."""
        resp = client.post(
            "/v1/messages",
            json={
                "model": "atlas-plan",
                "max_tokens": 50,
                "messages": [{"role": "user", "content": "Hi"}],
            },
            headers={
                "x-api-key": VALID_KEY,
                "anthropic-version": "2023-06-01",
                "anthropic-beta": "interleaved-thinking-2025-05-14",
            },
        )
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Integration: smoke test with live backend (skipped if not running)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestLiveBackend:
    """Run these with: pytest -m integration tests/test_messages_endpoint.py
    Requires: backend running on localhost:8001
    """

    BASE = "http://localhost:8001"
    KEY = "sk-atlas-FwcHfmI5qWzbohi2prMoixYBHAxEoxKEtN4qK2K9i38"

    def test_live_non_streaming(self):
        import httpx

        resp = httpx.post(
            f"{self.BASE}/v1/messages",
            json={
                "model": "Atlas",
                "max_tokens": 50,
                "messages": [{"role": "user", "content": "What is 2+2? Answer in one word."}],
            },
            headers={"x-api-key": self.KEY},
            timeout=30,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["type"] == "message"
        text_blocks = [b for b in body["content"] if b.get("type") == "text"]
        assert text_blocks, "No text content in response"
        print(f"\n[live] Response: {text_blocks[0]['text'][:100]}")

    def test_live_streaming(self):
        import httpx

        events = []
        with httpx.stream(
            "POST",
            f"{self.BASE}/v1/messages",
            json={
                "model": "Atlas",
                "max_tokens": 80,
                "stream": True,
                "messages": [{"role": "user", "content": "Say hello in exactly 5 words."}],
            },
            headers={"x-api-key": self.KEY},
            timeout=60,
        ) as resp:
            assert resp.status_code == 200, f"Status {resp.status_code}"
            current: dict = {}
            for line in resp.iter_lines():
                if line.startswith("event:"):
                    current["event"] = line[len("event:") :].strip()
                elif line.startswith("data:"):
                    try:
                        current["data"] = json.loads(line[len("data:") :].strip())
                    except Exception:
                        pass
                elif line == "" and current:
                    events.append(current)
                    current = {}

        event_types = [e.get("event") for e in events]
        print(f"\n[live-stream] Events: {event_types}")
        assert "message_start" in event_types
        assert "content_block_delta" in event_types
        assert "message_stop" in event_types

    def test_live_tool_result_multiturn(self):
        """Multi-turn conversation with tool_result block."""
        import httpx

        resp = httpx.post(
            f"{self.BASE}/v1/messages",
            json={
                "model": "Atlas",
                "max_tokens": 150,
                "messages": [
                    {"role": "user", "content": "What does the file say?"},
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_live_01",
                                "name": "read_file",
                                "input": {"path": "README.md"},
                            }
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_live_01",
                                "content": "# Atlas by Alpheric\nAI middleware platform.",
                            }
                        ],
                    },
                ],
            },
            headers={"x-api-key": self.KEY},
            timeout=30,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["type"] == "message"
        print(f"\n[live-tool_result] Response: {body['content']}")
