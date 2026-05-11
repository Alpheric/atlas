# 01 - Repository Map

---

## Repository Structure

```
A1_Trainer/
|-- src/a1/                    # Core Python backend (~12.5K LOC)
|   |-- app.py                 # FastAPI entry + lifespan hooks
|   |-- dependencies.py        # Redis + ARQ + DB session factories
|   |-- providers/             # LLM provider abstraction (8 implementations)
|   |-- proxy/                 # OpenAI-compatible API layer (3 routers)
|   |-- routing/               # Task classification + model selection
|   |-- training/              # Distillation + QLoRA pipeline
|   |-- agents/                # Agent registry + executor + planner
|   |-- chat/                  # WebSocket team chat
|   |-- dashboard/             # Admin API (30+ endpoints)
|   |-- session/               # Conversation memory (LRU + Redis)
|   |-- security/              # PII masking
|   |-- feedback/              # Argilla human annotation sync
|   |-- db/                    # SQLAlchemy models + repositories
|   |-- importers/             # Data import (OpenAI format, Paperclip)
|   |-- tools/                 # Computer use (browser + desktop)
|   |-- notebook/              # Cell execution + AI suggestions
|   |-- common/                # Auth, logging, metrics, telemetry, tokens, tz
|
|-- dashboard-ui/              # React + Vite frontend (~5K LOC)
|   |-- src/pages/             # 10 pages (Overview, Analytics, Conversations, etc.)
|   |-- src/components/        # Shared components (DataTable, FormModal, etc.)
|   |-- src/stores/            # Zustand (auth, notifications, theme, websocket)
|   |-- src/lib/               # API client (Axios), exports, constants
|   |-- src/types/             # TypeScript interfaces
|
|-- tests/                     # 4 test files (~50 tests)
|-- alembic/versions/          # 7 migration files
|-- config/                    # settings.py + providers.yaml + routing_policy.yaml
|-- scripts/                   # Dev/test utilities
|-- blueprint/                 # Architecture audit (this folder)
|-- docker-compose.yml         # Dev stack (Postgres, Redis, Ollama, ARQ, Argilla, Jaeger)
|-- docker-compose.prod.yml    # Production overrides
|-- Dockerfile                 # Python 3.12 slim container
|-- pyproject.toml             # Build + dependencies
|-- CLAUDE.md                  # Developer guidance
|-- PRD.md                     # Product requirements
|-- SUMMARY.md                 # Architecture overview
```

---

## Module-by-Module Explanation

### src/a1/providers/ -- LLM Provider Abstraction

| File | LOC | Purpose |
|------|-----|---------|
| base.py | 64 | Abstract LLMProvider interface + ModelInfo dataclass |
| registry.py | 282 | Singleton registry with health checks and model discovery |
| claude_cli.py | 562 | Routes through local `claude` CLI (OAuth, streaming, stdin for large payloads) |
| anthropic.py | ~140 | Native Anthropic SDK client |
| openai.py | ~120 | Native OpenAI SDK client |
| vertex.py | ~100 | Google Vertex AI client |
| ollama.py | ~250 | Multi-server Ollama with model-to-server routing |
| litellm_provider.py | ~180 | LiteLLM wrapper for 100+ models |
| openclaw.py | 309 | OpenClaw gateway integration |
| key_pool.py | ~200 | Encrypted multi-account API key rotation |

**Ownership:** AI Infrastructure
**Assessment:** Well-structured. Base abstraction is clean. Individual implementations vary in error handling quality.

### src/a1/proxy/ -- Request/Response Layer

| File | LOC | Purpose |
|------|-----|---------|
| router.py | ~50 | Aggregates sub-routers |
| openai_router.py | 335 | /v1/chat/completions + /v1/models |
| atlas_router.py | 350 | /atlas auto-routing endpoint |
| responses_router.py | 640 | OpenAI Responses API format |
| request_models.py | ~100 | Pydantic request schemas |
| response_models.py | ~150 | Pydantic response schemas |
| pipeline.py | ~200 | Shared pipeline helpers (_load_session, _mask_pii, etc.) |
| orchestrator.py | ~150 | Dual execution dispatch |
| cache.py | ~170 | GPTCache semantic + TaskResponseCache TTL |
| stream.py | ~100 | SSE chunk formatting |
| middleware.py | ~50 | Request interceptors |

**Ownership:** Platform Backend
**Assessment:** Three routers share logic but implement independently. Significant duplication. pipeline.py has some shared helpers but not a unified execution path.

### src/a1/routing/ -- Model Selection

| File | LOC | Purpose |
|------|-----|---------|
| classifier.py | ~200 | 10-task-type rule-based classifier with ML fallback |
| strategy.py | ~150 | Score-based model selection with exploration |
| scorer.py | ~150 | Cold-start defaults + quality/cost/latency scoring |
| features.py | ~80 | Feature extraction from requests |
| atlas_models.py | ~80 | Atlas model-to-task-type mapping |
| fallback.py | ~60 | Fallback chain logic |

**Ownership:** AI Infrastructure
**Assessment:** Solid design. Classifier is effective. Strategy pattern with live scoring after 20+ samples is good.

### src/a1/training/ -- Distillation Pipeline

| File | LOC | Purpose |
|------|-----|---------|
| auto_trainer.py | 924 | Core distillation: teacher/student execution, similarity, training triggers |
| trainer.py | ~200 | QLoRA training via Unsloth |
| collector.py | ~100 | Sample collection from conversations |
| dataset.py | ~150 | Dataset formatting for training |
| deployer.py | ~100 | Deploy adapters to Ollama |
| evaluator.py | ~120 | Model evaluation framework |
| harness_evaluator.py | ~150 | lm-evaluation-harness integration |
| tasks.py | ~80 | ARQ job definitions |

**Ownership:** AI Infrastructure
**Assessment:** auto_trainer.py is the largest single file. Does too many things (provider selection, execution, scoring, training triggers, handoff management, lifecycle state machine). Should be split.

### src/a1/db/ -- Database Layer

| File | LOC | Purpose |
|------|-----|---------|
| engine.py | ~40 | Async SQLAlchemy engine setup |
| models.py | 701 | 20+ ORM models with SQLite/PostgreSQL compatibility |
| repositories.py | 261 | Data access layer (5 repository classes) |

**Ownership:** Platform Backend
**Assessment:** Models are comprehensive. Missing workspace isolation on several key tables. Repository layer is thin but functional.

### src/a1/dashboard/ -- Admin API

| File | LOC | Purpose |
|------|-----|---------|
| router.py | 1219 | 30+ admin endpoints + WebSocket live feed |

**Ownership:** Platform Backend / Frontend
**Assessment:** God-file. 1219 LOC in a single router. Should be split into sub-routers (analytics, agents, applications, workspaces, training, plans).

---

## Key Apps and Services

| Service | Entry Point | Port | Purpose |
|---------|-------------|------|---------|
| Atlas API | src/a1/app.py | 8001 | Main FastAPI server |
| Dashboard UI | dashboard-ui/src/main.tsx | 5173 | Vite dev server |
| ARQ Worker | (via docker-compose) | - | Async job processing |
| PostgreSQL | (via docker-compose) | 5432 | Production database |
| Redis | (via docker-compose) | 6379 | Sessions + job queue |
| Ollama Server 1 | 10.0.0.9:11434 | 11434 | Code models |
| Ollama Server 2 | 10.0.0.10:11434 | 11434 | QA/reasoning models |

---

## Likely Ownership Boundaries

| Area | Owner | Files |
|------|-------|-------|
| Provider abstraction | AI Infra | providers/, routing/ |
| Request pipeline | Platform Backend | proxy/, pipeline |
| Distillation/training | AI Infra | training/ |
| Admin API | Platform Backend | dashboard/ |
| Auth/security | Security | common/auth.py, security/ |
| Database schema | Platform Backend | db/ |
| Dashboard UI | Frontend | dashboard-ui/ |
| Deployment | DevOps | Dockerfile, docker-compose.* |
| Agents/planning | AI Infra | agents/ |
| Chat/notebook/tools | Product | chat/, notebook/, tools/ |

---

## Cleanliness and Modularity Assessment

**Clean:**
- Provider abstraction (base.py + registry.py)
- Routing module (classifier, strategy, scorer)
- Session manager (well-encapsulated)
- PII masker (self-contained)

**Needs cleanup:**
- dashboard/router.py (1219 LOC god-file)
- auto_trainer.py (924 LOC doing too many things)
- Three proxy routers duplicating core logic
- 15 empty __init__.py files (no module-level exports)

**Dead or dormant:**
- importers/paperclip.py (no active usage path)
- importers/openai_format.py (no dashboard UI trigger)
- tools/computer.py (gated, no integration tests)
- notebook/ (prototype, no production path)
