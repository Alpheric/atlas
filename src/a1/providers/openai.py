import uuid
from collections.abc import AsyncIterator

import openai

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


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self):
        # Strip X-Stainless-* telemetry headers that the OpenAI SDK injects by default.
        # These headers add overhead through Cloudflare WAF inspection and are not
        # needed for API functionality.
        client_kwargs: dict = {
            "api_key": settings.openai_api_key,
            "default_headers": {
                "X-Stainless-Lang": "",
                "X-Stainless-Package-Version": "",
                "X-Stainless-Runtime": "",
                "X-Stainless-Runtime-Version": "",
                "X-Stainless-Arch": "",
                "X-Stainless-OS": "",
            },
        }
        base_url = settings.provider_base_url("openai")
        if base_url:
            client_kwargs["base_url"] = base_url
        self.client = openai.AsyncOpenAI(**client_kwargs)
        self._models = [
            ModelInfo("gpt-4o", "openai", 128000, 0.005, 0.015, True, True),
            ModelInfo("gpt-4o-mini", "openai", 128000, 0.00015, 0.0006, True, True),
            ModelInfo("o3-mini", "openai", 200000, 0.0011, 0.0044, True, True),
        ]

    def _build_kwargs(self, request: ChatCompletionRequest) -> dict:
        messages = [{"role": m.role, "content": m.content or ""} for m in request.messages]
        kwargs = {"model": request.model, "messages": messages}
        if request.max_tokens is not None:
            kwargs["max_tokens"] = request.max_tokens
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.top_p is not None:
            kwargs["top_p"] = request.top_p
        if request.tools:
            kwargs["tools"] = [t.model_dump() for t in request.tools]
        if request.tool_choice:
            kwargs["tool_choice"] = request.tool_choice
        if request.stop:
            kwargs["stop"] = request.stop
        return kwargs

    async def complete(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        kwargs = self._build_kwargs(request)
        response = await self.client.chat.completions.create(**kwargs)

        choice = response.choices[0]
        tool_calls = None
        if choice.message.tool_calls:
            tool_calls = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in choice.message.tool_calls
            ]

        return ChatCompletionResponse(
            id=response.id,
            model=response.model,
            choices=[
                Choice(
                    message=ChoiceMessage(content=choice.message.content, tool_calls=tool_calls),
                    finish_reason=choice.finish_reason,
                )
            ],
            usage=Usage(
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                total_tokens=response.usage.total_tokens,
            ),
            provider=self.name,
        )

    async def stream(self, request: ChatCompletionRequest) -> AsyncIterator[ChatCompletionChunk]:
        kwargs = self._build_kwargs(request)
        kwargs["stream"] = True
        chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

        stream = await self.client.chat.completions.create(**kwargs)
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            yield ChatCompletionChunk(
                id=chunk.id or chunk_id,
                model=chunk.model or request.model,
                choices=[
                    StreamChoice(
                        delta=DeltaMessage(
                            role=delta.role,
                            content=delta.content,
                        ),
                        finish_reason=chunk.choices[0].finish_reason,
                    )
                ],
            )

    async def health_check(self) -> bool:
        try:
            await self.client.models.list()
            return True
        except Exception:
            return False

    def supports_model(self, model: str) -> bool:
        return any(m.name == model for m in self._models)

    def list_models(self) -> list[ModelInfo]:
        return self._models
