import time
import uuid

from pydantic import BaseModel, Field


class ChoiceMessage(BaseModel):
    role: str = "assistant"
    content: str | None = None
    tool_calls: list[dict] | None = None


class Choice(BaseModel):
    index: int = 0
    message: ChoiceMessage
    finish_reason: str | None = "stop"


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionResponse(BaseModel):
    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex[:12]}")
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str = ""
    choices: list[Choice] = []
    usage: Usage = Usage()

    # A1 extensions
    provider: str | None = None
    task_type: str | None = None
    routing_strategy: str | None = None
    grounding_metadata: dict | None = None


class AtlasError(BaseModel):
    """Standard error response used by all Atlas endpoints."""

    # "provider_error", "validation_error", "auth_error", "internal_error", "rate_limit_error"
    error: str
    message: str
    request_id: str | None = None
    status_code: int = 500


class DeltaMessage(BaseModel):
    role: str | None = None
    content: str | None = None
    tool_calls: list[dict] | None = None


class StreamChoice(BaseModel):
    index: int = 0
    delta: DeltaMessage
    finish_reason: str | None = None


class ChatCompletionChunk(BaseModel):
    id: str = ""
    object: str = "chat.completion.chunk"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str = ""
    choices: list[StreamChoice] = []
    usage: Usage | None = None  # populated in final chunk when provider reports it
