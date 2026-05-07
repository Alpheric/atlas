import uuid
from collections.abc import AsyncIterator

import anthropic

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


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self):
        kwargs: dict = {"api_key": settings.anthropic_api_key}
        base_url = settings.provider_base_url("anthropic")
        if base_url:
            kwargs["base_url"] = base_url
        self.client = anthropic.AsyncAnthropic(**kwargs)
        self._models = [
            ModelInfo("claude-sonnet-4-20250514", "anthropic", 200000, 0.003, 0.015, True, True),
            ModelInfo("claude-haiku-4-5-20251001", "anthropic", 200000, 0.001, 0.005, True, True),
        ]

    def _split_system(self, messages: list) -> tuple[str | None, list[dict]]:
        system = None
        filtered = []
        for m in messages:
            if m.role == "system":
                system = m.content or ""
            else:
                filtered.append({"role": m.role, "content": m.content or ""})
        return system, filtered

    def _translate_tools(self, tools: list) -> list[dict]:
        result = []
        for t in tools:
            result.append(
                {
                    "name": t.function.name,
                    "description": t.function.description or "",
                    "input_schema": t.function.parameters or {"type": "object", "properties": {}},
                }
            )
        return result

    async def complete(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        system, messages = self._split_system(request.messages)
        kwargs = {
            "model": request.model,
            "messages": messages,
            "max_tokens": request.max_tokens or 4096,
        }
        if system:
            kwargs["system"] = system
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.tools:
            kwargs["tools"] = self._translate_tools(request.tools)

        response = await self.client.messages.create(**kwargs)

        # Extract text content
        content = ""
        tool_calls = None
        for block in response.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                if tool_calls is None:
                    tool_calls = []
                tool_calls.append(
                    {
                        "id": block.id,
                        "type": "function",
                        "function": {"name": block.name, "arguments": str(block.input)},
                    }
                )

        return ChatCompletionResponse(
            model=request.model,
            choices=[Choice(message=ChoiceMessage(content=content, tool_calls=tool_calls))],
            usage=Usage(
                prompt_tokens=response.usage.input_tokens,
                completion_tokens=response.usage.output_tokens,
                total_tokens=response.usage.input_tokens + response.usage.output_tokens,
            ),
            provider=self.name,
        )

    async def stream(self, request: ChatCompletionRequest) -> AsyncIterator[ChatCompletionChunk]:
        system, messages = self._split_system(request.messages)
        kwargs = {
            "model": request.model,
            "messages": messages,
            "max_tokens": request.max_tokens or 4096,
        }
        if system:
            kwargs["system"] = system
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature

        chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

        async with self.client.messages.stream(**kwargs) as stream:
            # First chunk with role
            yield ChatCompletionChunk(
                id=chunk_id,
                model=request.model,
                choices=[StreamChoice(delta=DeltaMessage(role="assistant"))],
            )

            async for text in stream.text_stream:
                yield ChatCompletionChunk(
                    id=chunk_id,
                    model=request.model,
                    choices=[StreamChoice(delta=DeltaMessage(content=text))],
                )

            # Final chunk
            yield ChatCompletionChunk(
                id=chunk_id,
                model=request.model,
                choices=[StreamChoice(delta=DeltaMessage(), finish_reason="stop")],
            )

    async def health_check(self) -> bool:
        try:
            await self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1,
                messages=[{"role": "user", "content": "hi"}],
            )
            return True
        except Exception:
            return False

    def supports_model(self, model: str) -> bool:
        return any(m.name == model for m in self._models)

    def list_models(self) -> list[ModelInfo]:
        return self._models
