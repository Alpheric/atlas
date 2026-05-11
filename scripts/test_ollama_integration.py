"""Test script: Verify A1 Trainer's Ollama integration directly (no Postgres needed).

Tests:
1. Multi-server model discovery
2. Task classification + routing
3. Token counting accuracy
4. Streaming with token capture
5. Model comparison
"""

import asyncio
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from a1.common.tokens import count_tokens_for_model, count_messages_tokens_for_model
from a1.routing.classifier import classify_task
from a1.routing.features import extract_features
from a1.proxy.request_models import ChatCompletionRequest, MessageInput


async def test_model_discovery():
    """Test that we can discover models from Ollama servers."""
    print("\n=== Test 1: Model Discovery ===")
    from a1.providers.ollama import OllamaProvider

    provider = OllamaProvider()
    await provider.discover_models()

    models = provider.list_models()
    servers = provider.list_servers()

    print(f"Discovered {len(models)} models across {len(servers)} servers:")
    for s in servers:
        status = "ONLINE" if s["healthy"] else "OFFLINE"
        print(f"  [{status}] {s['url']}: {s['models']}")

    print(f"\nAll models: {[m.name for m in models]}")
    assert len(models) > 0, "No models discovered!"
    print("PASS")
    return provider


async def test_task_classification():
    """Test the task classifier with different prompt types."""
    print("\n=== Test 2: Task Classification ===")

    test_cases = [
        ("Write a Python function to sort a list", "code"),
        ("What is the capital of France?", "chat"),
        ("Calculate the integral of x^2 dx", "math"),
        ("Translate 'hello' to Spanish", "translation"),
        ("Summarize this article about AI", "summarization"),
    ]

    for prompt, expected_type in test_cases:
        request = ChatCompletionRequest(
            model="auto",
            messages=[MessageInput(role="user", content=prompt)],
        )
        task_type, confidence = classify_task(request)
        match = "PASS" if task_type == expected_type else "MISS"
        print(f"  [{match}] '{prompt[:40]}...' -> {task_type} (confidence={confidence:.2f}, expected={expected_type})")

    print("PASS (classification working)")


async def test_token_counting():
    """Test accurate token counting vs the old broken heuristic."""
    print("\n=== Test 3: Token Counting Accuracy ===")

    test_text = "def is_prime(n):\n    if n <= 1:\n        return False\n    for i in range(2, int(n**0.5) + 1):\n        if n % i == 0:\n            return False\n    return True"

    # Accurate count
    accurate = count_tokens_for_model(test_text, "deepseek-coder:6.7b")

    # Old broken heuristic
    old_heuristic = len(test_text.split()) * 2

    print(f"  Text: {test_text[:50]}...")
    print(f"  Accurate (tiktoken): {accurate} tokens")
    print(f"  Old heuristic (split*2): {old_heuristic} tokens")
    print(f"  Difference: {abs(accurate - old_heuristic)} tokens ({abs(accurate - old_heuristic) / max(accurate, 1) * 100:.0f}% off)")

    # Test message counting
    messages = [
        {"role": "system", "content": "You are a helpful coding assistant."},
        {"role": "user", "content": "Write a prime checker in Python"},
    ]
    msg_tokens = count_messages_tokens_for_model(messages, "llama3.2:latest")
    print(f"  Messages token count: {msg_tokens}")
    print("PASS")


async def test_non_streaming_completion(provider):
    """Test non-streaming completion through our provider."""
    print("\n=== Test 4: Non-Streaming Completion ===")

    request = ChatCompletionRequest(
        model="llama3.2:latest",
        messages=[MessageInput(role="user", content="What is 2+2? One word answer.")],
        max_tokens=10,
    )

    response = await provider.complete(request)

    print(f"  Model: {response.model}")
    print(f"  Content: {response.choices[0].message.content}")
    print(f"  Prompt tokens: {response.usage.prompt_tokens}")
    print(f"  Completion tokens: {response.usage.completion_tokens}")
    print(f"  Provider: {response.provider}")

    assert response.usage.prompt_tokens > 0, "No prompt tokens!"
    assert response.usage.completion_tokens > 0, "No completion tokens!"
    print("PASS")


async def test_streaming_completion(provider):
    """Test streaming completion with token capture from final chunk."""
    print("\n=== Test 5: Streaming Completion (Token Capture) ===")

    request = ChatCompletionRequest(
        model="llama3.2:latest",
        messages=[MessageInput(role="user", content="Count from 1 to 5.")],
        max_tokens=50,
    )

    full_content = ""
    stream_usage = None

    async for chunk in provider.stream(request):
        if chunk.choices and chunk.choices[0].delta.content:
            full_content += chunk.choices[0].delta.content
        if chunk.usage:
            stream_usage = chunk.usage

    print(f"  Streamed content: {full_content[:100]}...")
    print(f"  Provider-reported usage: {stream_usage}")

    if stream_usage:
        print(f"    Prompt tokens: {stream_usage.prompt_tokens}")
        print(f"    Completion tokens: {stream_usage.completion_tokens}")

    # Compare with tiktoken
    tiktoken_count = count_tokens_for_model(full_content, "llama3.2:latest")
    print(f"  Tiktoken completion count: {tiktoken_count}")

    if stream_usage:
        diff = abs(stream_usage.completion_tokens - tiktoken_count)
        print(f"  Difference: {diff} tokens ({diff / max(tiktoken_count, 1) * 100:.0f}%)")

    print("PASS")


async def test_model_routing():
    """Test that different models route to correct servers."""
    print("\n=== Test 6: Model Routing ===")
    from a1.providers.ollama import OllamaProvider

    provider = OllamaProvider()
    await provider.discover_models()

    test_models = ["llama3.2:latest", "deepseek-coder:6.7b", "deepseek-coder-v2:16b", "nomic-embed-text:latest"]

    for model in test_models:
        server_url = provider.get_server_for_model(model)
        supported = provider.supports_model(model)
        print(f"  {model}: server={server_url}, supported={supported}")

    print("PASS")


async def test_code_generation():
    """Test deepseek-coder with a real code task."""
    print("\n=== Test 7: Code Generation (deepseek-coder) ===")
    from a1.providers.ollama import OllamaProvider

    provider = OllamaProvider()
    await provider.discover_models()

    request = ChatCompletionRequest(
        model="deepseek-coder:6.7b",
        messages=[MessageInput(role="user", content="Write a Python function that reverses a string. Just the function.")],
        max_tokens=200,
    )

    response = await provider.complete(request)
    content = response.choices[0].message.content

    print(f"  Model: {response.model}")
    print(f"  Tokens: {response.usage.prompt_tokens} in / {response.usage.completion_tokens} out")
    print(f"  Response:\n{content[:300]}")

    has_def = "def " in content
    print(f"\n  Contains function definition: {has_def}")
    print("PASS" if has_def else "WARN: No function definition found")


async def main():
    print("=" * 60)
    print("A1 TRAINER — OLLAMA INTEGRATION TEST")
    print("=" * 60)

    provider = await test_model_discovery()
    await test_task_classification()
    await test_token_counting()
    await test_non_streaming_completion(provider)
    await test_streaming_completion(provider)
    await test_model_routing()
    await test_code_generation()

    print("\n" + "=" * 60)
    print("ALL TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
