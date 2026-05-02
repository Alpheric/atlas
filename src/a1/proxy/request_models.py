from pydantic import BaseModel, field_validator


class FunctionDef(BaseModel):
    name: str
    description: str | None = None
    parameters: dict | None = None


class ToolDef(BaseModel):
    type: str = "function"
    function: FunctionDef


class MessageInput(BaseModel):
    role: str
    content: str | list | None = None  # str or multimodal array
    name: str | None = None
    tool_calls: list[dict] | None = None
    tool_call_id: str | None = None

    @field_validator("content", mode="before")
    @classmethod
    def normalize_content(cls, v):
        """Normalize OpenAI/Anthropic multimodal content arrays to plain strings.

        Handles:
          OpenAI:    [{"type": "text", "text": "hello"}, {"type": "image_url", ...}]
          Anthropic: [{"type": "tool_result", "tool_use_id": "...", "content": "..."}]
                     [{"type": "tool_use", "id": "...", "name": "...", "input": {...}}]
        """
        if isinstance(v, list):
            parts = []
            for item in v:
                if isinstance(item, dict):
                    t = item.get("type", "")
                    if t == "text":
                        parts.append(item.get("text", ""))
                    elif t == "image_url":
                        pass  # Skip images — Atlas doesn't support vision yet
                    elif t == "tool_result":
                        # Anthropic tool_result: extract text from content field
                        inner = item.get("content", "")
                        if isinstance(inner, list):
                            inner_parts = [
                                b.get("text", "")
                                for b in inner
                                if isinstance(b, dict) and b.get("type") == "text"
                            ]
                            parts.append("\n".join(p for p in inner_parts if p))
                        elif isinstance(inner, str):
                            parts.append(inner)
                    elif t == "tool_use":
                        pass  # Skip tool_use blocks (assistant turn artifacts)
                    else:
                        text_val = item.get("text", item.get("content", ""))
                        if text_val:
                            parts.append(str(text_val))
                elif isinstance(item, str):
                    parts.append(item)
            return "\n".join(p for p in parts if p)
        return v


class ChatCompletionRequest(BaseModel):
    model: str = "auto"
    messages: list[MessageInput]
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    stream: bool = False
    tools: list[ToolDef] | None = None
    tool_choice: str | dict | None = None
    stop: str | list[str] | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    n: int | None = 1
    user: str | None = None

    # A1 extensions
    strategy: str | None = None  # best_quality, lowest_cost, lowest_latency
    conversation_id: str | None = None
    session_id: str | None = None
    previous_response_id: str | None = None
