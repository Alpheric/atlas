"""Deep test: Hit every model through A1 Trainer proxy on both servers.

Tests:
1. All 6 models via direct model name
2. Auto-routing for each task type
3. Streaming vs non-streaming
4. Response headers (X-A1-*)
5. Dashboard metrics after all requests
"""

import asyncio
import json
import time
import sys
import httpx

API = "http://localhost:8000"

results = []


async def send_request(label, model, prompt, max_tokens=50, stream=False):
    """Send a request through the proxy and capture results."""
    start = time.time()
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "stream": stream,
    }

    async with httpx.AsyncClient(timeout=300.0) as client:
        try:
            if stream:
                full_content = ""
                usage_data = None
                async with client.stream("POST", f"{API}/v1/chat/completions", json=body) as resp:
                    async for line in resp.aiter_lines():
                        if line.startswith("data: ") and line != "data: [DONE]":
                            chunk = json.loads(line[6:])
                            if chunk.get("usage"):
                                usage_data = chunk["usage"]
                            elif chunk.get("choices") and chunk["choices"][0].get("delta", {}).get("content"):
                                full_content += chunk["choices"][0]["delta"]["content"]

                latency = int((time.time() - start) * 1000)
                tokens_in = usage_data["prompt_tokens"] if usage_data else "?"
                tokens_out = usage_data["completion_tokens"] if usage_data else "?"
                r = {
                    "label": label, "model": model, "stream": True,
                    "latency_ms": latency, "tokens_in": tokens_in, "tokens_out": tokens_out,
                    "content": full_content[:100], "status": "PASS",
                }
            else:
                resp = await client.post(f"{API}/v1/chat/completions", json=body)
                latency = int((time.time() - start) * 1000)

                # Check headers
                headers = {k: v for k, v in resp.headers.items() if k.startswith("x-a1")}

                if resp.status_code != 200:
                    r = {"label": label, "model": model, "stream": False,
                         "latency_ms": latency, "status": "FAIL", "error": resp.text[:100]}
                else:
                    d = resp.json()
                    r = {
                        "label": label, "model": model, "stream": False,
                        "provider": d.get("provider"), "task_type": d.get("task_type"),
                        "latency_ms": latency,
                        "tokens_in": d["usage"]["prompt_tokens"],
                        "tokens_out": d["usage"]["completion_tokens"],
                        "content": d["choices"][0]["message"]["content"][:100],
                        "headers": headers, "status": "PASS",
                    }
        except Exception as e:
            latency = int((time.time() - start) * 1000)
            r = {"label": label, "model": model, "latency_ms": latency,
                 "status": "FAIL", "error": str(e)[:100]}

    results.append(r)
    status = r["status"]
    lat = r["latency_ms"]
    prov = r.get("provider", "")
    task = r.get("task_type", "")
    tin = r.get("tokens_in", "?")
    tout = r.get("tokens_out", "?")
    content = r.get("content", r.get("error", ""))[:60]
    stream_tag = "[STREAM]" if r.get("stream") else ""
    print(f"  [{status}] {label:35s} | {lat:6d}ms | {prov:8s} | {task:15s} | {str(tin):>4s}in/{str(tout):>4s}out | {content} {stream_tag}")


async def main():
    print("=" * 100)
    print("A1 TRAINER — DEEP TEST (BOTH SERVERS)")
    print("=" * 100)

    # --- Part 1: Direct model requests (hit every model) ---
    print("\n--- PART 1: Direct Model Requests (all 6 inference models) ---")
    print(f"  {'Label':35s} | {'Lat':>8s} | {'Provider':8s} | {'Task':15s} | {'Tokens':>12s} | Response")

    await send_request("llama3.2 (server1)", "llama3.2:latest", "What is Python?", 30)
    await send_request("deepseek-coder:6.7b (server1)", "deepseek-coder:6.7b", "Write hello world in Rust", 50)
    await send_request("deepseek-coder-v2:16b (server1)", "deepseek-coder-v2:16b", "Explain async/await in Python", 50)
    await send_request("codellama:13b (server2)", "codellama:13b", "What is a linked list?", 50)
    await send_request("mistral:7b (server2)", "mistral:7b", "What is Docker used for?", 50)
    await send_request("deepseek-r1:8b (server2)", "deepseek-r1:8b", "If a car travels 80km/h for 2.5 hours, how far?", 100)

    # --- Part 2: Auto-routing by task type ---
    print("\n--- PART 2: Auto-Routing (smart task classification) ---")
    print(f"  {'Label':35s} | {'Lat':>8s} | {'Provider':8s} | {'Task':15s} | {'Tokens':>12s} | Response")

    await send_request("auto: chat", "auto", "Hello, how are you today?", 30)
    await send_request("auto: code", "auto", "Write a Python function to sort a list using quicksort", 100)
    await send_request("auto: math", "auto", "Calculate the derivative of x^3 + 2x^2", 50)
    await send_request("auto: summarize", "auto", "Summarize: Machine learning is a subset of AI that enables systems to learn from data.", 50)
    await send_request("auto: translate", "auto", "Translate 'good morning' to French, Spanish, and German", 50)
    await send_request("auto: analysis", "auto", "Analyze the pros and cons of microservices architecture versus monolithic design.", 80)

    # --- Part 3: Streaming test ---
    print("\n--- PART 3: Streaming (token capture from stream) ---")
    print(f"  {'Label':35s} | {'Lat':>8s} | {'Provider':8s} | {'Task':15s} | {'Tokens':>12s} | Response")

    await send_request("stream: llama3.2", "llama3.2:latest", "Count from 1 to 10", 50, stream=True)
    await send_request("stream: deepseek-coder", "deepseek-coder:6.7b", "Write a binary search in Python", 100, stream=True)
    await send_request("stream: mistral", "mistral:7b", "What are the SOLID principles?", 80, stream=True)

    # --- Part 4: Check response headers ---
    print("\n--- PART 4: Response Headers ---")
    for r in results:
        if r.get("headers") and r["status"] == "PASS":
            print(f"  {r['label']:35s} | Headers: {r['headers']}")

    # --- Part 5: Dashboard metrics ---
    print("\n--- PART 5: Dashboard Metrics ---")
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{API}/admin/metrics")
        m = resp.json()
        print(f"  Total Requests:    {m['request_count']}")
        print(f"  Total Errors:      {m['error_count']}")
        print(f"  Avg Latency:       {m['avg_latency_ms']:.0f}ms")
        print(f"  Total Cost:        ${m['total_cost_usd']}")
        print(f"  Providers:         {m['provider_counts']}")
        print(f"  Models:            {m['model_counts']}")
        print(f"  Task Types:        {m['task_type_counts']}")
        print(f"  LOCAL:             {m['local']['request_count']} reqs | {m['local']['total_tokens']} tokens (FREE)")
        print(f"  EXTERNAL:          {m['external']['request_count']} reqs | {m['external']['total_tokens']} tokens (${m['external']['cost_usd']})")
        print(f"  SAVINGS:           ${m['savings_usd']}")

    # --- Summary ---
    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    print(f"\n{'=' * 100}")
    print(f"RESULTS: {passed} PASSED, {failed} FAILED out of {len(results)} tests")

    if failed:
        print("\nFailed tests:")
        for r in results:
            if r["status"] == "FAIL":
                print(f"  {r['label']}: {r.get('error', 'unknown')}")

    print("=" * 100)


if __name__ == "__main__":
    asyncio.run(main())
