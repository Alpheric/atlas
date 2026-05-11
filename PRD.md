# PRD — A1 Trainer (OneDesk AI/LLM Middleware)

**Product**: A1 Trainer — Alpheric AI Middleware
**Version**: 0.2.0 Enterprise
**Owner**: Neeraj @ Alpheric
**Date**: 2026-03-27
**Status**: Live (Development)

---

## 1. Vision

A1 Trainer is enterprise AI middleware that acts as a **smart proxy and brain** between applications and LLM providers. It routes every request to the best model for the job — preferring free local models — while continuously collecting data to improve local model quality through fine-tuning. The unified endpoint exposes **Alpheric-1**, a virtual model that abstracts away the complexity of multi-model, multi-server infrastructure.

## 2. Problem Statement

Organizations using LLMs face:
1. **Vendor lock-in**: Tied to one provider (OpenAI, Anthropic, etc.)
2. **Cost opacity**: No visibility into per-request token costs across providers
3. **No local-first strategy**: Paying for external APIs when local models could handle most tasks
4. **Scattered chat history**: Conversations spread across OpenClaw, ChatGPT, Claude, etc.
5. **No feedback loop**: External model usage doesn't improve internal capabilities
6. **Manual model selection**: Users must pick which model to use for each task

## 3. Solution

A1 Trainer solves all of these with a single OpenAI-compatible proxy:

| Problem | Solution |
|---------|----------|
| Vendor lock-in | LiteLLM adapter supports 100+ models; switch with one config change |
| Cost opacity | Real-time token tracking (in/out), cost per request, savings vs external |
| No local-first | Smart router prefers local Ollama models; falls back to external only when needed |
| Scattered history | Import from OpenClaw, Paperclip.ing, JSONL; unified conversation store |
| No feedback loop | Training pipeline: collect → train → evaluate → deploy improved models |
| Manual selection | Alpheric-1 auto-routes based on task classification + performance data |

## 4. Target Users

| User | Use Case |
|------|----------|
| **Developers** | Point `OPENAI_BASE_URL` at A1 Trainer; get smart routing + local-first automatically |
| **AI/ML Engineers** | Monitor model performance, trigger fine-tuning, evaluate with benchmarks |
| **Platform Admins** | Manage API keys, set budgets, monitor costs, configure routing policies |
| **Team Leads** | View analytics, cost savings, conversation quality signals |

## 5. Current State (v0.2.0)

### 5.1 Infrastructure — LIVE
| Component | Location | Status |
|-----------|----------|--------|
| A1 Trainer API | 10.0.0.2:8000 | ✅ Running |
| Dashboard UI | localhost:5173 | ✅ Running |
| Ollama Server 1 | 10.0.0.9:11434 | ✅ 4 models (code generation) |
| Ollama Server 2 | 10.0.0.10:11434 | ✅ 3 models (QA/reasoning) |
| OpenClaw Gateway | 10.0.0.11:18789 | ✅ Gateway running |

### 5.2 Models Available (12)
| Model | Type | Provider | Context |
|-------|------|----------|---------|
| **alpheric-1** | Virtual (smart-routed) | alpheric | 128K |
| auto / auto:fast / auto:cheap | Virtual (strategy-based) | a1-trainer | 200K |
| local | Virtual (any local) | a1-trainer | 4K |
| deepseek-coder-v2:16b | Code generation | ollama (10.0.0.9) | 4K |
| deepseek-coder:6.7b | Code generation | ollama (10.0.0.9) | 4K |
| llama3.2:latest | General chat | ollama (10.0.0.9) | 4K |
| nomic-embed-text:latest | Embeddings | ollama (10.0.0.9) | 4K |
| codellama:13b | Code analysis | ollama (10.0.0.10) | 4K |
| deepseek-r1:8b | Reasoning | ollama (10.0.0.10) | 4K |
| mistral:7b | General reasoning | ollama (10.0.0.10) | 4K |

### 5.3 Features — Completed
- [x] OpenAI-compatible proxy (`/v1/chat/completions`, `/v1/models`)
- [x] Alpheric-1 unified model with smart routing
- [x] Multi-server Ollama provider (auto-discovery, health monitoring)
- [x] OpenClaw provider integration (model proxy + chat history import)
- [x] LiteLLM provider engine (100+ external models)
- [x] Task classifier (code, chat, math, analysis, creative, etc.)
- [x] Strategy-based routing (best_quality, lowest_cost, lowest_latency)
- [x] Epsilon-greedy exploration (10% for training data)
- [x] Fallback chain on provider failure
- [x] Token counting in/out (tiktoken, provider-reported)
- [x] Local vs external usage tracking with savings calculation
- [x] GPTCache semantic caching
- [x] Multi-account key pool (round_robin, budget_aware)
- [x] 10-page enterprise dashboard (dark mode)
- [x] WebSocket live feed for real-time monitoring
- [x] Conversation persistence + replay
- [x] Quality signals (thumbs up/down, scores)
- [x] Training pipeline scaffolding (Unsloth QLoRA)
- [x] lm-eval-harness evaluation
- [x] Argilla human feedback integration
- [x] Paperclip.ing + JSONL import
- [x] OpenTelemetry tracing (optional)
- [x] SQLite (dev) / PostgreSQL (prod) dual database support

## 6. Roadmap

### Phase 1 — Production Hardening (Current)
| Feature | Priority | Status |
|---------|----------|--------|
| OpenClaw chat history import (with auth) | High | In Progress |
| Docker Compose full stack | High | Scaffolded |
| PostgreSQL migration | High | Ready (alembic) |
| API key authentication | Medium | Implemented (optional) |
| HTTPS + reverse proxy (nginx) | Medium | Config ready |

### Phase 2 — Enterprise Dashboard Improvements
| Feature | Priority | Description |
|---------|----------|-------------|
| Token usage charts (time-series) | High | Hourly/daily/weekly token in/out by model |
| Cost breakdown dashboard | High | Per-provider, per-model cost trends |
| Local vs External pie chart improvements | High | Interactive with drill-down |
| Multi-model comparison UI | High | Side-by-side response comparison |
| Usage heatmap | Medium | Request volume by hour/day |
| Model performance leaderboard | Medium | Auto-updating based on quality signals |
| Alert rules | Medium | Notify on error spikes, latency degradation |
| Export to CSV/PDF | Medium | Downloadable reports |
| Role-based access control | Low | Admin, viewer, operator roles |

### Phase 3 — Smart Training Pipeline
| Feature | Priority | Description |
|---------|----------|-------------|
| Auto-collect high-quality training data | High | Filter by quality signals + diversity |
| One-click fine-tuning | High | Dashboard button → Unsloth pipeline |
| A/B testing (new vs old model) | High | Split traffic for comparison |
| Benchmark dashboard | Medium | MMLU, HumanEval, HellaSwag results over time |
| Training data browser | Medium | Browse, filter, annotate training examples |
| Scheduled re-training | Low | Weekly auto-retrain based on new data |

### Phase 4 — External Model Integration
| Feature | Priority | Description |
|---------|----------|-------------|
| Claude API integration | High | Anthropic as fallback provider |
| OpenAI Codex integration | High | Code-specific external routing |
| Google Vertex (Gemini) | Medium | Multi-cloud support |
| Cost-aware routing with budgets | Medium | Monthly budget caps per provider |
| Rate limit handling + retry | Medium | Automatic retry with backoff |

### Phase 5 — Alpheric Teams Integration
| Feature | Priority | Description |
|---------|----------|-------------|
| Multi-tenant support | High | Team isolation, per-team budgets |
| SSO integration | Medium | Enterprise auth |
| Audit logging | Medium | Who used what model, when, how much |
| API usage metering | Medium | Per-user/team token quotas |
| Governance policies | Low | Model allowlists, content filtering |

## 7. Technical Specifications

### 7.1 API Contract
All proxy endpoints follow the OpenAI Chat Completions API spec. Additional headers:
- `X-A1-Provider`: Which provider handled the request
- `X-A1-Is-Local`: Whether a local model was used
- `X-A1-Cost`: Estimated cost in USD
- `X-A1-Tokens-In` / `X-A1-Tokens-Out`: Token counts
- `X-A1-Cache`: "hit" or "miss"

### 7.2 Database Schema (10 tables)
| Table | Purpose |
|-------|---------|
| conversations | Chat sessions |
| messages | Individual messages |
| routing_decisions | Per-message routing metadata |
| quality_signals | Human/automated feedback |
| model_performance | Aggregated hourly metrics |
| training_runs | Fine-tuning jobs |
| provider_accounts | Multi-account keys |
| usage_records | Per-request usage (billing) |
| usage_hourly_rollups | Pre-aggregated analytics |
| api_keys | Proxy authentication tokens |

### 7.3 Provider Plugin Architecture
Adding a new provider requires:
1. Create `src/a1/providers/myprovider.py`
2. Inherit from `LLMProvider` abstract base class
3. Implement: `complete()`, `stream()`, `health_check()`, `supports_model()`, `list_models()`
4. Register in `ProviderRegistry.initialize()`
5. Add models to `config/providers.yaml`

### 7.4 Routing Policy
Defined in `config/routing_policy.yaml`:
```yaml
code:
  model: deepseek-coder-v2:16b      # Prefer local
  fallback:
    - deepseek-coder:6.7b            # Local fallback
    - codellama:13b                  # Different server
    - claude-sonnet-4-20250514       # External (paid)
chat:
  model: llama3.2:latest
  fallback:
    - mistral:7b
    - claude-haiku-4-5-20251001
```

## 8. Success Metrics

| Metric | Target | Current |
|--------|--------|---------|
| Local routing % | >80% | 100% (dev) |
| Avg latency (local) | <3s | 2.3s (warm) |
| Cost savings vs external | >90% | 100% (all local) |
| Provider uptime | >99% | 100% (Ollama) |
| Models available | >10 | 12 |
| Dashboard pages | 10 | 10 |
| Training pipeline | Automated | Scaffolded |

## 9. Non-Functional Requirements

| Requirement | Spec |
|-------------|------|
| Availability | 99.9% (local infrastructure) |
| Latency overhead | <50ms added by proxy layer |
| Concurrent users | 100+ (FastAPI async) |
| Data retention | Unlimited (SQLite/PostgreSQL) |
| Security | API key auth, encrypted key storage, CORS |
| Observability | OpenTelemetry traces, in-memory metrics, live WebSocket feed |

## 10. Codebase Stats

| Metric | Value |
|--------|-------|
| Total files | 87 |
| Total LOC | ~8,100 |
| Python backend | 51 files / 5,172 LOC |
| TypeScript dashboard | 33 files / 2,493 LOC |
| Config | 6 files / 434 LOC |
| Dashboard pages | 10 |
| API endpoints | 25+ |
| Database tables | 10 |
| Providers | 6 (Ollama, OpenClaw, Anthropic, OpenAI, Vertex, LiteLLM) |
| Open-source integrations | 7 (LiteLLM, Unsloth, GPTCache, OpenTelemetry, Argilla, lm-eval-harness, OpenClaw) |
