import json as _json

from pydantic import BaseModel, field_validator, model_validator


class FunctionDef(BaseModel):
    name: str
    description: str | None = None
    parameters: dict | None = None


class ToolDef(BaseModel):
    type: str = "function"
    function: FunctionDef | None = None  # None for special types like code_interpreter


class MessageInput(BaseModel):
    role: str
    content: str | list | None = None  # str or multimodal array
    content_parts: list | None = None  # raw parts preserved when message has image_url items
    name: str | None = None
    tool_calls: list[dict] | None = None
    tool_call_id: str | None = None

    @property
    def has_images(self) -> bool:
        """True if this message contains image_url content parts."""
        return bool(self.content_parts)

    @model_validator(mode="before")
    @classmethod
    def fix_anthropic_tool_roles(cls, values: dict) -> dict:
        """Normalise Anthropic tool roles and preserve image content before text flattening.

        1. Anthropic tool results (role=user, content=[{type:tool_result}]) → role=tool
        2. Messages with image_url parts have their raw parts saved in content_parts
           so vision-capable providers (Vertex/Gemini) can use them directly.
        """
        if not isinstance(values, dict):
            return values

        role = values.get("role", "")
        content = values.get("content")

        if role == "user" and isinstance(content, list) and content:
            # Anthropic tool_result promotion
            if all(isinstance(i, dict) and i.get("type") == "tool_result" for i in content):
                values = dict(values)
                values["role"] = "tool"
                if not values.get("tool_call_id"):
                    values["tool_call_id"] = content[0].get("tool_use_id")

        # Preserve raw parts when content list contains images
        if isinstance(content, list) and "content_parts" not in values:
            has_image = any(
                isinstance(i, dict) and i.get("type") == "image_url"
                for i in content
            )
            if has_image:
                values = dict(values)
                values["content_parts"] = content

        return values

    @field_validator("content", mode="before")
    @classmethod
    def normalize_content(cls, v):
        """Flatten multimodal content arrays to plain strings for text-only providers.

        Images are skipped here — vision-capable providers read content_parts instead.

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
                        parts.append("[image]")  # placeholder so text context isn't empty
                    elif t == "tool_result":
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
                        name = item.get("name", "unknown")
                        input_data = item.get("input", {})
                        tag_json = _json.dumps({"name": name, "input": input_data})
                        parts.append(f"<tool_call>{tag_json}</tool_call>")
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
    metadata: dict | None = None  # internal routing hints (e.g. {"web_search": True})
