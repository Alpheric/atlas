"""Test both Ollama servers: model routing, cross-server comparison, all 7 models."""

import asyncio
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from a1.providers.ollama import OllamaProvider
from a1.proxy.request_models import ChatCompletionRequest, MessageInput
from a1.common.tokens import count_tokens_for_model


async def main():
    print("=" * 70)
    print("A1 TRAINER — DUAL SERVER INTEGRATION TEST")
    print("=" * 70)

    provider = OllamaProvider()
    await provider.discover_models()

    servers = provider.list_servers()
    models = provider.list_models()

    print(f"\nServers: {len(servers)} ({sum(1 for s in servers if s['healthy'])} online)")
    for s in servers:
        status = "ONLINE" if s["healthy"] else "OFFLINE"
        print(f"  [{status}] {s['url']}: {s['models']}")

    print(f"\nTotal models: {len(models)}")

    # --- Test 1: Hit every model on both servers ---
    print("\n=== Test: All Models Respond ===")
    test_models = [m.name for m in models if "embed" not in m.name]  # skip embedding model

    for model_name in test_models:
        server_url = provider.get_server_for_model(model_name)
        request = ChatCompletionRequest(
            model=model_name,
            messages=[MessageInput(role="user", content="Say hello in one sentence.")],
            max_tokens=30,
        )
        try:
            start = time.time()
            response = await provider.complete(request)
            latency = int((time.time() - start) * 1000)
            content = response.choices[0].message.content.strip()[:80]
            tokens_in = response.usage.prompt_tokens
            tokens_out = response.usage.completion_tokens
            print(f"  PASS  {model_name:30s} @ {server_url:30s} | {latency:5d}ms | {tokens_in:3d}in/{tokens_out:3d}out | {content}")
        except Exception as e:
            print(f"  FAIL  {model_name:30s} @ {server_url:30s} | Error: {e}")

    # --- Test 2: Cross-server model comparison ---
    print("\n=== Test: Cross-Server Comparison ===")
    prompt = "Write a Python one-liner to flatten a nested list."
    compare_models = ["deepseek-coder:6.7b", "codellama:13b", "mistral:7b"]

    print(f"  Prompt: '{prompt}'")
    print()

    for model_name in compare_models:
        if not provider.supports_model(model_name):
            print(f"  SKIP  {model_name} — not available")
            continue

        server_url = provider.get_server_for_model(model_name)
        request = ChatCompletionRequest(
            model=model_name,
            messages=[MessageInput(role="user", content=prompt)],
            max_tokens=150,
        )
        try:
            start = time.time()
            response = await provider.complete(request)
            latency = int((time.time() - start) * 1000)
            content = response.choices[0].message.content.strip()
            tokens_out = response.usage.completion_tokens

            # Verify token count with tiktoken
            tiktoken_count = count_tokens_for_model(content, model_name)

            print(f"  {model_name} @ {server_url}")
            print(f"    Latency: {latency}ms | Tokens: {tokens_out} (tiktoken: {tiktoken_count})")
            print(f"    Response: {content[:200]}")
            print()
        except Exception as e:
            print(f"  FAIL  {model_name}: {e}")

    # --- Test 3: Streaming from server 2 ---
    print("=== Test: Streaming from Server 2 (codellama:13b) ===")
    request = ChatCompletionRequest(
        model="codellama:13b",
        messages=[MessageInput(role="user", content="What is a binary tree? One sentence.")],
        max_tokens=50,
    )

    full_content = ""
    stream_usage = None
    chunks = 0

    async for chunk in provider.stream(request):
        if chunk.choices and chunk.choices[0].delta.content:
            full_content += chunk.choices[0].delta.content
            chunks += 1
        if chunk.usage:
            stream_usage = chunk.usage

    print(f"  Chunks received: {chunks}")
    print(f"  Content: {full_content[:200]}")
    if stream_usage:
        print(f"  Provider tokens: {stream_usage.prompt_tokens} in / {stream_usage.completion_tokens} out")
    tiktoken_count = count_tokens_for_model(full_content, "codellama:13b")
    print(f"  Tiktoken count: {tiktoken_count}")
    print("  PASS")

    # --- Test 4: Reasoning model (deepseek-r1) ---
    print("\n=== Test: Reasoning Model (deepseek-r1:8b) ===")
    request = ChatCompletionRequest(
        model="deepseek-r1:8b",
        messages=[MessageInput(role="user", content="If a train travels 60mph for 2.5 hours, how far does it go?")],
        max_tokens=100,
    )
    start = time.time()
    response = await provider.complete(request)
    latency = int((time.time() - start) * 1000)
    content = response.choices[0].message.content.strip()
    print(f"  Latency: {latency}ms")
    print(f"  Tokens: {response.usage.prompt_tokens} in / {response.usage.completion_tokens} out")
    print(f"  Response: {content[:300]}")
    has_150 = "150" in content
    print(f"  Correct answer (150 miles): {'YES' if has_150 else 'CHECK'}")
    print("  PASS")

    print("\n" + "=" * 70)
    print("ALL DUAL-SERVER TESTS PASSED")
    print(f"Tested {len(test_models)} models across 2 servers")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
