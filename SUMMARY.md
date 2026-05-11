# A1 Trainer — OneDesk AI/LLM Middleware & Training Platform

## What It Does
Enterprise AI middleware that acts as a smart proxy and brain between your applications and LLM providers — both external (Claude, GPT, Gemini, Codex, Vertex, 100+ models) and local (Ollama across multiple servers). It routes each request to the best model for the job, tracks token usage in/out, collects quality data, and uses it to continuously improve local models through fine-tuning.

**Alpheric-1** is the unified model name exposed by A1 Trainer — it auto-routes to the best available local LLM via smart routing.

## Architecture

```
                        ┌────────────────────────────────────────────────┐
                        │              A1 Trainer Proxy                   │
  Any OpenAI-           │              (Alpheric-1 Engine)                │
  compatible   ───────► │                                                │
  client                │  Classify ► Route ► Cache ► Call ► Track       │
  (apps, IDEs,          │     │         │       │      │       │         │
   OpenClaw,            │     ▼         ▼       ▼      ▼       ▼         │
   agents)              │  Task Type  Best    GPT    Provider  Usage     │
                        │  (code,     Model   Cache  (local    Record    │
                        │   chat,     (by     (hit/  or ext)   (tokens   │
                        │   math...)  score)  miss)            in/out)   │
                        └──────────────┬────────────────┬───────────────┘
                                       │                │
                  ┌────────────────────┼────────────────┼───────────────┐
                  ▼                    ▼                ▼               ▼
           ┌──────────┐        ┌─────────────┐  ┌──────────┐  ┌────────────┐
           │ Ollama   │        │ External    │  │ OpenClaw │  │ SQLite/    │
           │ Server 1 │        │ Providers   │  │ Gateway  │  │ PostgreSQL │
           │ 10.0.0.9 │        │ (Claude,    │  │10.0.0.11 │  │ (history,  │
           │ 4 models │        │  GPT, etc.) │  │          │  │  metrics)  │
           └──────────┘        └─────────────┘  └──────────┘  └────────────┘
           ┌──────────┐               │
           │ Ollama   │               ▼
           │ Server 2 │        ┌──────────────────────┐
           │10.0.0.10 │        │   Training Pipeline   │
           │ 3 models │        │  Collect ► Train ►    │
           └──────────┘        │  Evaluate ► Deploy    │
                               │  (Unsloth QLoRA)      │
                               └──────────────────────┘
```

## Live Infrastructure

| Component | URL | Status | Details |
|-----------|-----|--------|---------|
| **A1 Trainer API** | `http://10.0.0.2:8000` | ✅ Running | OpenAI-compatible proxy |
| **Dashboard UI** | `http://localhost:5173` | ✅ Running | 10-page enterprise dashboard |
| **Ollama Server 1** | `http://10.0.0.9:11434` | ✅ Healthy | deepseek-coder-v2:16b, deepseek-coder:6.7b, llama3.2, nomic-embed-text |
| **Ollama Server 2** | `http://10.0.0.10:11434` | ✅ Healthy | codellama:13b, deepseek-r1:8b, mistral:7b |
| **OpenClaw Gateway** | `http://10.0.0.11:18789` | ✅ Running | Chat history source + model proxy |

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python FastAPI (async) |
| Provider Engine | LiteLLM (100+ models, auto-translation) |
| Local Models | Ollama (2 servers, 7 models) |
| AI Gateway | OpenClaw (chat history import, model proxy) |
| Database | SQLite (dev) / PostgreSQL (prod) + SQLAlchemy async |
| Cache/Queue | Redis + GPTCache (semantic) |
| Training | Unsloth (2-5x faster QLoRA) + HuggingFace |
| Evaluation | lm-evaluation-harness (MMLU, HumanEval, etc.) |
| Feedback | Argilla (human annotation) |
| Observability | OpenTelemetry → Jaeger/Grafana |
| Dashboard | React + Vite + AntDesign + Recharts |

## Project Structure

```
A1_Trainer/
├── src/a1/
│   ├── app.py             # FastAPI entry + lifespan hooks
│   ├── dependencies.py    # DB session management
│   ├── proxy/             # OpenAI-compatible /v1/chat/completions
│   │   ├── router.py      # Main proxy endpoint + Alpheric-1 routing
│   │   ├── request_models.py
│   │   ├── response_models.py
│   │   ├── cache.py       # GPTCache semantic layer
│   │   ├── middleware.py   # Request/response interceptors
│   │   └── stream.py      # SSE streaming
│   ├── providers/         # LLM provider abstraction layer
│   │   ├── base.py        # LLMProvider abstract class
│   │   ├── registry.py    # Provider registry singleton
│   │   ├── ollama.py      # Multi-server Ollama (2 servers, 7 models)
│   │   ├── openclaw.py    # OpenClaw gateway integration
│   │   ├── litellm_provider.py  # LiteLLM unified (100+ models)
│   │   ├── key_pool.py    # Multi-account load balancer
│   │   ├── anthropic.py   # Legacy native Anthropic
│   │   ├── openai.py      # Legacy native OpenAI
│   │   └── vertex.py      # Legacy native Vertex
│   ├── routing/           # Smart ML-based routing
│   │   ├── classifier.py  # Task type classification
│   │   ├── strategy.py    # Model selection (quality/cost/latency)
│   │   ├── scorer.py      # Cold-start defaults + scoring
│   │   ├── features.py    # Feature extraction
│   │   └── fallback.py    # Fallback chain logic
│   ├── training/          # Fine-tuning pipeline
│   │   ├── trainer.py     # Unsloth QLoRA trainer
│   │   ├── deployer.py    # Deploy to Ollama
│   │   ├── collector.py   # High-quality example collection
│   │   ├── dataset.py     # Dataset preparation
│   │   ├── evaluator.py   # Automated evaluation
│   │   ├── harness_evaluator.py  # lm-eval-harness integration
│   │   └── tasks.py       # Background job scheduling
│   ├── feedback/          # Human feedback
│   │   └── argilla_sync.py
│   ├── importers/         # Data import
│   │   ├── paperclip.py   # Paperclip.ing import
│   │   └── openai_format.py
│   ├── dashboard/         # Admin API
│   │   └── router.py      # 25+ admin endpoints + WebSocket live feed
│   ├── db/                # Database layer
│   │   ├── engine.py      # SQLAlchemy async engine
│   │   ├── models.py      # 10 ORM models
│   │   └── repositories.py
│   └── common/            # Shared utilities
│       ├── auth.py        # Bearer token validation
│       ├── logging.py     # Structured logging
│       ├── metrics.py     # In-memory metrics tracking
│       ├── telemetry.py   # OpenTelemetry integration
│       └── tokens.py      # Token counting (tiktoken)
├── dashboard-ui/          # React SPA (10 pages)
│   ├── src/pages/         # Overview, Conversations, Routing, Providers,
│   │                      # Accounts, Models, Training, Analytics, Import, Settings
│   ├── src/components/    # Layout, Auth, Shared components
│   ├── src/stores/        # Zustand state (auth, theme, websocket, notifications)
│   └── src/lib/           # API client, constants, export utils
├── config/
│   ├── settings.py        # Pydantic settings (env-based)
│   ├── providers.yaml     # Provider + model definitions
│   └── routing_policy.yaml # Task→model routing defaults
├── alembic/               # Database migrations
├── tests/                 # Pytest test suite
├── docker-compose.yml     # Multi-container orchestration
└── .env                   # Environment configuration
```

## Key Features

### Alpheric-1 — Unified Model
- Smart-routed virtual model that auto-picks the best local LLM
- Exposed as `alpheric-1` on all OpenAI-compatible endpoints
- Supports `auto`, `auto:fast`, `auto:cheap`, `local` routing aliases
- Zero cost — always routes to local models first

### Smart Proxy (OpenAI-compatible)
- Drop-in replacement: point `OPENAI_BASE_URL` at A1 Trainer
- Token counting in/out with accurate per-model tracking
- Local vs external usage tracking with cost savings calculation
- Streaming + non-streaming support
- Request/response logging to DB with full conversation history

### Smart Router
- Rule-based task classifier (code, chat, math, analysis, creative, summarization, translation, structured_extraction)
- Model scorer picks best provider by quality, cost, or latency
- Epsilon-greedy exploration (10%) to keep discovering better models
- Fallback chain: same provider → different provider → local model
- Health-aware: automatically routes around unhealthy providers

### Multi-Server Ollama
- 2 dedicated GPU servers on local network
- Server 1 (10.0.0.9): Code models — deepseek-coder-v2:16b, deepseek-coder:6.7b, llama3.2, nomic-embed-text
- Server 2 (10.0.0.10): QA/reasoning — codellama:13b, deepseek-r1:8b, mistral:7b
- Auto-discovery, health monitoring, model-to-server routing

### OpenClaw Integration
- Connected to OpenClaw gateway at 10.0.0.11:18789
- Chat history import for training data collection
- Model proxy for unified access
- Registered as a provider in the A1 Trainer registry

### Multi-Account Key Pool
- Multiple API keys per provider (e.g., 3 Anthropic accounts)
- Load balancing: round_robin, least_used, priority, budget_aware
- Encrypted key storage (Fernet)
- Temporary blacklist after rate limit/errors

### Training Pipeline
- Collects high-quality conversations (filtered by user feedback)
- QLoRA fine-tuning via Unsloth (2-5x faster than vanilla HuggingFace)
- Evaluation via lm-eval-harness (MMLU, HellaSwag, HumanEval)
- Auto-deploy to Ollama if improvement > 2%

### Enterprise Dashboard (10 Pages)
- **Overview**: KPI cards (requests, latency, cost, errors), provider/task charts, live feed, provider health
- **Conversations**: Search, browse, replay with routing decisions + quality signals
- **Routing**: Decision log, task type breakdown, model performance
- **Providers**: Health cards, model inventory, OpenClaw status
- **Accounts**: Multi-account key management, budget limits, usage tracking
- **Models**: Full model catalog with context windows, performance comparison
- **Training**: Job queue, run history, eval metrics, deployment
- **Analytics**: Cost breakdown, latency trends, local vs external, token usage
- **Import**: Paperclip.ing, OpenClaw, JSONL import wizard
- **Settings**: Provider keys, routing policy, feature toggles

### Open-Source Integrations (all feature-flagged)
| Tool | Purpose | Flag |
|------|---------|------|
| LiteLLM | 100+ model support | `A1_USE_LITELLM=true` |
| Unsloth | 2-5x faster training | `A1_USE_UNSLOTH=true` |
| GPTCache | Semantic caching | `A1_CACHE_ENABLED=true` |
| OpenTelemetry | Traces + metrics | `A1_OTLP_ENDPOINT=` |
| Argilla | Human feedback | `A1_ARGILLA_API_URL=` |
| lm-eval-harness | Benchmarks | `A1_USE_HARNESS_EVAL=false` |
| OpenClaw | AI gateway + chat history | `A1_OPENCLAW_URL=` |

## API Endpoints

### Proxy (OpenAI-compatible)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/chat/completions` | POST | Main proxy — supports alpheric-1, auto, auto:fast, auto:cheap, local, or any model |
| `/v1/models` | GET | List all available models (12+ including virtual models) |
| `/health` | GET | Health check |

### Admin Dashboard
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/admin/overview` | GET | KPIs, provider health, metrics snapshot |
| `/admin/conversations` | GET | Paginated conversation list |
| `/admin/conversations/{id}` | GET | Conversation detail + messages + routing |
| `/admin/conversations/{id}/feedback` | POST | Add quality signal |
| `/admin/routing/decisions` | GET | Recent routing decisions |
| `/admin/routing/performance` | GET | Model leaderboard by task type |
| `/admin/providers` | GET | Provider list + health |
| `/admin/providers/refresh` | POST | Re-check all providers |
| `/admin/accounts` | GET/POST/DELETE | Multi-account key management |
| `/admin/training/runs` | GET/POST | Training run management |
| `/admin/training/runs/{id}/evaluate` | POST | Trigger lm-eval-harness |
| `/admin/analytics/local-vs-external` | GET | Local vs external usage |
| `/admin/analytics/latency` | GET | Latency percentiles by model |
| `/admin/analytics/errors` | GET | Error counts by provider |
| `/admin/ollama/models` | GET | Ollama model inventory + servers |
| `/admin/ollama/pull` | POST | Pull new model to Ollama |
| `/admin/models/compare` | POST | Side-by-side model comparison |
| `/admin/openclaw/status` | GET | OpenClaw gateway status |
| `/admin/openclaw/import-history` | POST | Import chat history from OpenClaw |
| `/admin/openclaw/discover` | POST | Discover OpenClaw models |
| `/admin/argilla/status` | GET | Argilla connection status |
| `/admin/argilla/export` | POST | Export to Argilla |
| `/admin/argilla/import` | POST | Import annotations |
| `/admin/import/paperclip` | POST | Import from paperclip.ing |
| `/admin/import/jsonl` | POST | Import JSONL file |
| `/admin/metrics` | GET | Full metrics snapshot |
| `/admin/ws/live-feed` | WS | Real-time request stream |

## Quick Start

```bash
# 1. Create venv + install
python -m venv .venv
.venv/Scripts/pip install -e .          # or pip install -e ".[dev]"
cd dashboard-ui && npm install && cd ..

# 2. Configure
cp .env.example .env                    # Edit Ollama server IPs, API keys

# 3. Run
.venv/Scripts/python -m uvicorn src.a1.app:app --host 0.0.0.0 --port 8000
cd dashboard-ui && npm run dev          # Dashboard on :5173

# 4. Test Alpheric-1
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "alpheric-1", "messages": [{"role": "user", "content": "Hello"}]}'
```

## File Count
- **87 files**, **~8,100 lines of code**
- Backend: 51 Python files (5,172 LOC)
- Dashboard: 33 TypeScript/TSX files (2,493 LOC)
- Config: 6 files (434 LOC)
