# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Backend (Python)
```bash
# Install (editable, from project root)
.venv/Scripts/python -m pip install -e .            # core
.venv/Scripts/python -m pip install -e ".[dev]"      # + pytest, ruff, pytest-cov
.venv/Scripts/python -m pip install -e ".[training]" # + unsloth, datasets, lm-eval, argilla

# Run backend (MUST set PYTHONIOENCODING for Claude CLI emoji handling on Windows)
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m uvicorn src.a1.app:app --host 0.0.0.0 --port 8001

# Single test file
.venv/Scripts/python -m pytest tests/test_core_pipeline.py -v

# All tests
.venv/Scripts/python -m pytest tests/ -v

# Lint / format
ruff check src/ tests/
ruff format src/ tests/
```

### Dashboard (React + Vite)
```bash
cd dashboard-ui
npm install
npm run dev    # :5173 with HMR — proxies to http://localhost:8001
npm run build  # production build → dist/
```
The Vite proxy in `dashboard-ui/vite.config.ts` must match the backend port (currently 8001).

### Database Migrations
```bash
alembic upgrade head                          # apply all pending migrations
alembic downgrade base                        # roll back everything (dev only)
alembic revision --autogenerate -m "message"  # generate migration from model diff
alembic history                               # show migration chain
alembic current                               # show current DB revision
```

**Migration chain:** `0000_initial_schema` → `0001_add_max_local_pct` → …

**Adding a new migration:** edit `src/a1/db/models.py`, run `alembic revision --autogenerate`, review the generated file in `alembic/versions/` (autogenerate is imperfect), then `alembic upgrade head`. Never use `create_all` against a prod DB.

## Architecture

**Alpheric.AI** is an enterprise AI middleware platform with the **Atlas** model family. It routes requests through Claude (via local CLI) as the "teacher", streams responses to users in real-time, and trains local Ollama models in the background to progressively handle requests independently.

### Atlas Model Family
7 models, each mapping to a task type and preferred local Ollama fallback:
- `atlas-plan` (chat/planning), `atlas-code` (code), `atlas-secure` (security), `atlas-infra` (devops), `atlas-data` (analytics), `atlas-books` (writing), `atlas-audit` (compliance)
- `alpheric-1` is a legacy alias for `atlas-plan`

### Request Flow — Unified CorePipeline

All three API entry points normalize to `CorePipelineInput` and run through `proxy/core_pipeline.py:CorePipeline`:

| Entry Point | File |
|---|---|
| `POST /atlas` | `proxy/atlas_router.py` |
| `POST /v1/responses` | `proxy/responses_router.py` (OpenAI Responses API, used by Alpheric Teams) |
| `POST /v1/chat/completions` | `proxy/openai_router.py` |

**Pipeline steps** (`core_pipeline.py:CorePipeline.execute()`):
1. Resolve model aliases
2. Load session history (with `A1_SESSION_LOAD_GRACE_MS` timeout)
3. PII mask (external providers only)
4. Classify task type + resolve Atlas model
5. Cache check (`A1_TASK_CACHE_ENABLED`) — returns cached response for identical non-streaming requests
6. Budget check (workspace monthly token limit)
7. Task-repeat fast-routing — after `A1_DISTILLATION_TASK_REPEAT_THRESHOLD` requests of the same task type, routes directly to local Ollama
8. Execute via distillation or direct provider
9. PII unmask response
10. Cache store, session save, metrics record, DB persist

`CorePipelineResult` carries: `response_id`, `assistant_text`, `chunk_iterator` (streaming), `provider_name`, `atlas_model`, `task_type`, `tokens`, `cost`, `latency_ms`, `cache_hit`, `fast_path`, `distillation`, `pii_masked`.

### Distillation Pipeline (`training/auto_trainer.py`)
When `A1_DISTILLATION_ENABLED=true`:
1. Claude CLI answers (teacher) — returned to user immediately
2. Background: same request → best local Ollama model (student)
3. Jaccard similarity comparison → stored in `DualExecutionRecord` table
4. 100+ samples per task type → auto-triggers QLoRA fine-tuning
5. Successful training → handoff % increases (0% → 90% max)

Probabilistic routing: at 30% handoff, 30% of requests go local.

### Agent System (`agents/`)
- `AgentRegistry` (singleton): In-memory cache of agent definitions loaded from DB. Agents are keyed by ID or `workspace:name`. Fields: `atlas_model`, `system_prompt`, `tools`, `memory_config`, `parent_id`, `app_id`.
- `PlanningEngine`: CEO/Manager/Worker hierarchy. `atlas-plan` acts as CEO — breaks a goal into 2–7 subtasks, assigns each to the best Atlas model, executes in dependency order. Depth controlled by `A1_PLANNING_MAX_DEPTH` (default 3), parallelism by `A1_PLANNING_MAX_WORKERS` (default 5).
- `AgentExecutor`: Runs individual agent tasks and propagates results upward.

Agents are managed via `dashboard/agents_router.py` endpoints and can be triggered via `POST /agents/{agent_id}/run`.

### Session Memory (`session/manager.py`)
In-memory LRU cache (1000 sessions, 1hr TTL) with optional Redis backing (`A1_REDIS_URL`). Resolved by:
1. Explicit `session_id` in request
2. `previous_response_id` → looks up which session produced that response
3. Neither → new session

Token-budget enforcement: if injected history would exceed `A1_SESSION_MAX_HISTORY_TOKENS` (default 40,000), oldest messages are dropped. Sessions cleared on restart unless Redis is configured.

### Claude CLI Provider (`providers/claude_cli.py`)
- `--output-format json` for accurate token counts and cost
- `--system-prompt` injects Atlas identity
- True streaming via 80-byte stdout chunks
- **Windows:** `PYTHONIOENCODING=utf-8` required — Claude emojis crash cp1252 codec otherwise
- Falls back to `llama3.2:latest` if Claude CLI fails

### Provider Abstraction
All providers inherit `providers/base.py:LLMProvider`. Active providers:
- **`claude-cli`** — Claude Opus/Sonnet/Haiku via local CLI
- **`ollama`** — local models across GPU servers (10.0.0.9 + 10.0.0.10)

`providers/registry.py:provider_registry` (singleton) initializes at startup. Health is checked by `provider_registry.is_healthy(name)` — never call `.is_healthy()` directly on a provider instance (OllamaProvider doesn't have this method).

### PII Masking (`security/pii_masker.py`)
Detects email, phone, SSN, credit card, API keys, IPs, AWS credentials, passwords. Returns reversible `mask_map` so responses are unmasked before delivery. Only applied for external providers.

### Real-Time Features
- **WebSocket chat** (`chat/ws.py`): Per-channel rooms; JSON protocol with `type` (message|typing|error|ping). Messages broadcast to all members, then forwarded to Atlas for AI response. Endpoint: `WS /ws/chat/{channel_id}`.
- **Notebook** (`notebook/`): Jupyter-like cell-based execution. CRUD under `/notebooks` and `/cells`.

### SSE Streaming (`proxy/stream.py`)
- `sse_stream()` — OpenAI chat completion chunks
- `sse_responses_stream_live()` — OpenAI Responses API events (`response.output_text.delta`). Accepts a live `chunk_iterator` (true streaming) or `full_text` (simulated).

### Dashboard Backend
`dashboard/router.py` aggregates all sub-routers:

| Router | Handles |
|---|---|
| `agents_router.py` | Agents, applications, workspaces CRUD + `/run` |
| `analytics_router.py` | Overview metrics, time-series, routing heatmap |
| `conversations_router.py` | History, sessions, feedback, PII stats |
| `governance_router.py` | Compliance, approvals, audit logs |
| `plans_router.py` | Task planning API |
| `providers_router.py` | Provider management, Ollama servers, key accounts, playground |
| `training_router.py` | Training runs, distillation status, Argilla integration, dataset import |
| `auth_router.py` | Login / API key auth |

Auth: API key in `Authorization` header for HTTP; query-param `token` for WebSocket (browsers can't send headers during upgrade).

### Observability (`common/prometheus.py`)
Custom Prometheus-compatible `/metrics` endpoint (no `prometheus_client` dependency). Exports: request counters, provider/model counts per task type, token usage, cost, errors, provider health, latency percentiles.

`common/tz.py`: `now_ist()` returns current time in IST (UTC+5:30) — used for timestamps throughout.

### Key Singletons
| Singleton | Module | Purpose |
|---|---|---|
| `provider_registry` | `providers/registry.py` | Provider instances + health |
| `agent_registry` | `agents/registry.py` | Agent definitions cache |
| `session_manager` | `session/manager.py` | Conversation memory |
| `pii_masker` | `security/pii_masker.py` | PII detection engine |
| `metrics` | `common/metrics.py` | In-memory counters, leaderboard, history (resets on restart) |
| `settings` | `config/settings.py` | Pydantic BaseSettings, `A1_` env prefix |

### Database
SQLAlchemy async with `SQLiteUUID` TypeDecorator (UUID↔string). Dev: SQLite (`a1_trainer.db`). Prod: PostgreSQL.

Key tables: `conversations`, `messages`, `routing_decisions`, `dual_execution_records`, `task_type_readiness`, `usage_records`.

### Dashboard UI
React SPA (Ant Design + Recharts + Zustand). API client in `dashboard-ui/src/lib/api.ts`.

## Configuration

All settings in `config/settings.py` with `A1_` prefix (from `.env`):

| Group | Key Settings |
|---|---|
| Atlas models | `A1_ATLAS_MODELS` (JSON array) |
| Distillation | `A1_DISTILLATION_ENABLED`, `A1_DISTILLATION_CLAUDE_MODEL`, `A1_DISTILLATION_MIN_SAMPLES`, `A1_DISTILLATION_TASK_REPEAT_THRESHOLD` (default 10) |
| Session | `A1_SESSION_ENABLED`, `A1_SESSION_TTL_SECONDS`, `A1_SESSION_MAX_MESSAGES`, `A1_SESSION_MAX_HISTORY_TOKENS` (default 40000), `A1_SESSION_LOAD_GRACE_MS` (default 100), `A1_REDIS_URL` |
| PII | `A1_PII_MASKING_ENABLED`, `A1_PII_MASK_FOR_EXTERNAL_ONLY` |
| Caching | `A1_TASK_CACHE_ENABLED` |
| Agents | `A1_PLANNING_MAX_DEPTH` (default 3), `A1_PLANNING_MAX_WORKERS` (default 5), `A1_AGENT_EXECUTION_TIMEOUT` (default 120s) |
| Key pool | `A1_ENCRYPTION_KEY`, `A1_KEY_POOL_STRATEGY` (round_robin) |
| Ollama | `A1_OLLAMA_SERVERS` (JSON array of server URLs) |
| Routing | `config/routing_policy.yaml` (task→model defaults), `config/providers.yaml` (model metadata) |

## Conventions

- Async everywhere — DB, HTTP, subprocess all use `async/await`
- Ruff: line-length 100, target Python 3.11
- `alpheric-1` alias for `atlas-plan` is mapped in 3 places in `proxy/openai_router.py` — keep them in sync
- `provider_registry.is_healthy(name)` — use this, never `.is_healthy()` on a provider instance
- Windows: always start uvicorn with `PYTHONIOENCODING=utf-8`
- Dashboard: Ant Design components, Zustand stores, Axios in `lib/api.ts`
