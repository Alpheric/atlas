"""LiteLLM-backed provider — replaces individual Anthropic/OpenAI/Vertex implementations.

Uses litellm.acompletion() for unified access to 100+ LLM providers with
automatic request translation, retries, and timeout handling.
"""

import uuid
from collections.abc import AsyncIterator

import litellm

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

log = get_logger("providers.litellm")

# LiteLLM global config
litellm.num_retries = 2
litellm.request_timeout = 1200  # 20 min — matches agent_execution_timeout
litellm.drop_params = True  # silently drop unsupported params per provider


# Provider name → LiteLLM model prefix mapping
# LiteLLM requires specific prefixes for non-OpenAI models
PROVIDER_PREFIX_MAP = {
    "anthropic": "",  # LiteLLM auto-detects claude-* models
    "openai": "",  # No prefix needed
    "vertex": "vertex_ai/",  # Vertex models need prefix
    "bedrock": "bedrock/",
    "cohere": "cohere/",
    "mistral": "mistral/",
    "groq": "groq/",
    "together": "together_ai/",
    "deepseek": "deepseek/",
    "fireworks": "fireworks_ai/",
}


class LiteLLMProvider(LLMProvider):
    """Unified provider backed by LiteLLM SDK.

    Handles Anthropic, OpenAI, Vertex, and 100+ other providers through
    a single class by delegating API translation to LiteLLM.
    """

    def __init__(
        self,
        name: str,
        models: list[ModelInfo],
        api_key: str | None = None,
        api_base: str | None = None,
        prefix_override: str | None = None,
    ):
        self.name = name
        self._models = models
        self._api_key = api_key  # fallback single key from env
        self._api_base = api_base
        # Allow explicit prefix override (e.g. "gemini/" instead of "vertex_ai/"
        # when using a Google AI Studio API key rather than GCP service account)
        self._prefix = prefix_override if prefix_override is not None else PROVIDER_PREFIX_MAP.get(name, "")
        self._last_account_id = None  # track which key pool account was used
        self._last_account_name = None

    def _litellm_model(self, model: str) -> str:
        """Convert our model name to LiteLLM's expected format."""
        if self._prefix and not model.startswith(self._prefix):
            return f"{self._prefix}{model}"
        return model

    # X-Stainless-* headers are injected by the OpenAI SDK's Stainless-generated
    # client code on every request. They carry no functional value and trigger
    # Cloudflare WAF inspection, adding latency. Setting them to empty strings
    # suppresses them at the HTTP layer. Only applied for OpenAI-compatible endpoints.
    _STAINLESS_SUPPRESS = {
        "X-Stainless-Lang": "",
        "X-Stainless-Package-Version": "",
        "X-Stainless-Runtime": "",
        "X-Stainless-Runtime-Version": "",
        "X-Stainless-Arch": "",
        "X-Stainless-OS": "",
    }

    def _build_kwargs(self, request: ChatCompletionRequest) -> dict:
        """Build kwargs for litellm.acompletion()."""
        messages = [{"role": m.role, "content": m.content or ""} for m in request.messages]

        kwargs: dict = {
            "model": self._litellm_model(request.model),
            "messages": messages,
        }

        if self._api_key:
            kwargs["api_key"] = self._api_key
        if self._api_base:
            kwargs["api_base"] = self._api_base

        # Strip X-Stainless-* telemetry headers for OpenAI (and OpenAI-compatible) endpoints
        if self.name in ("openai", "groq", "moonshot", "deepseek", "fireworks", "together"):
            kwargs["extra_headers"] = self._STAINLESS_SUPPRESS

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
        response = await litellm.acompletion(**kwargs)

        # LiteLLM returns OpenAI-format ModelResponse
        choice = response.choices[0]
        tool_calls = None
        if hasattr(choice.message, "tool_calls") and choice.message.tool_calls:
            tool_calls = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in choice.message.tool_calls
            ]

        return ChatCompletionResponse(
            id=response.id or f"chatcmpl-{uuid.uuid4().hex[:12]}",
            model=request.model,
            choices=[
                Choice(
                    message=ChoiceMessage(
                        content=choice.message.content,
                        tool_calls=tool_calls,
                    ),
                    finish_reason=choice.finish_reason,
                )
            ],
            usage=Usage(
                prompt_tokens=response.usage.prompt_tokens if response.usage else 0,
                completion_tokens=response.usage.completion_tokens if response.usage else 0,
                total_tokens=response.usage.total_tokens if response.usage else 0,
            ),
            provider=self.name,
        )

    async def stream(self, request: ChatCompletionRequest) -> AsyncIterator[ChatCompletionChunk]:
        kwargs = self._build_kwargs(request)
        kwargs["stream"] = True

        chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        response = await litellm.acompletion(**kwargs)

        async for chunk in response:
            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta
            yield ChatCompletionChunk(
                id=chunk.id or chunk_id,
                model=request.model,
                choices=[
                    StreamChoice(
                        delta=DeltaMessage(
                            role=getattr(delta, "role", None),
                            content=getattr(delta, "content", None),
                        ),
                        finish_reason=chunk.choices[0].finish_reason,
                    )
                ],
            )

    async def health_check(self) -> bool:
        if not self._models:
            return False
        try:
            test_model = self._litellm_model(self._models[0].name)
            await litellm.acompletion(
                model=test_model,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1,
                api_key=self._api_key,
            )
            return True
        except Exception as e:
            log.warning(f"Health check failed for {self.name}: {e}")
            return False

    def supports_model(self, model: str) -> bool:
        return any(m.name == model for m in self._models)

    def list_models(self) -> list[ModelInfo]:
        return self._models

    def estimate_cost(self, prompt_tokens: int, completion_tokens: int, model: str) -> float:
        """Use LiteLLM's cost calculation if available, fallback to ModelInfo."""
        try:
            litellm_model = self._litellm_model(model)
            prompt_cost, completion_cost = litellm.cost_per_token(
                model=litellm_model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
            return prompt_cost + completion_cost
        except Exception:
            return super().estimate_cost(prompt_tokens, completion_tokens, model)
