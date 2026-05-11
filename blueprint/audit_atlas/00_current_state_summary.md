# 00 - Current State Summary

**Audit Date:** 2026-04-08
**Auditor Role:** Principal AI Platform Architect
**Repository:** A1_Trainer (Atlas by Alpheric.AI)

---

## What Atlas Currently Is

Atlas is a functional AI middleware proxy with a distillation training pipeline and an admin dashboard. It accepts OpenAI-compatible requests, classifies them by task type, routes them to the best available provider (Claude CLI, Ollama, Anthropic API, OpenAI, Groq, Vertex, LiteLLM), and returns responses via JSON or SSE streaming.

The distillation pipeline sends each request to both a "teacher" model (Claude/GPT) and a "student" model (local Ollama), compares outputs, and accumulates training data. When enough samples exist per task type, it can trigger QLoRA fine-tuning via Unsloth.

The system defines 7 Atlas model personas (atlas-plan, atlas-code, atlas-secure, atlas-infra, atlas-data, atlas-books, atlas-audit), each with a domain-specific system prompt and preferred provider chain.

Recent additions include: an agent registry with executor and planner, WebSocket team chat, a notebook execution engine, and computer-use tool definitions. These are structurally present but not yet production-hardened.

---

## Major Modules That Exist

| Module | LOC (est.) | Maturity | Purpose |
|--------|------------|----------|---------|
| providers/ | ~2,200 | Solid | 8 LLM provider implementations + registry |
| proxy/ | ~2,500 | Functional | 3 OpenAI-compatible entry points + pipeline |
| routing/ | ~600 | Solid | Task classification + model selection + scoring |
| training/ | ~1,800 | Functional | Distillation + QLoRA + evaluation + deployment |
| dashboard/ | ~1,200 | Functional | Admin API (30+ endpoints) |
| agents/ | ~800 | Early | Agent registry + executor + planner |
| session/ | ~300 | Solid | In-memory + Redis conversation memory |
| security/ | ~160 | Solid (narrow) | PII detection + masking |
| feedback/ | ~400 | Functional | Argilla human annotation integration |
| db/ | ~1,000 | Functional | 20+ SQLAlchemy models + repositories |
| chat/ | ~200 | Prototype | WebSocket team chat |
| notebook/ | ~400 | Prototype | Cell execution + AI suggestions |
| tools/ | ~250 | Prototype | Browser + desktop automation (gated) |
| common/ | ~700 | Solid | Auth, logging, metrics, telemetry, tokens |
| dashboard-ui/ | ~5,000 | Functional | React + Ant Design (10 pages) |

**Total backend:** ~12,500 LOC Python
**Total frontend:** ~5,000 LOC TypeScript

---

## Overall Maturity Assessment

**Stage: Late Prototype / Early MVP**

The core request routing and distillation pipeline works. The provider abstraction is well-designed. The dashboard provides real visibility. But the system lacks the foundational enterprise primitives (RBAC, multi-tenancy, workspace isolation) needed for production use beyond a single-operator deployment.

---

## Core Strengths

1. **Provider abstraction is clean.** `LLMProvider` base class with 8 implementations, health checks, cost estimation. The registry pattern is correct.

2. **Distillation pipeline is novel and functional.** Teacher/student dual execution with similarity scoring, sample collection, training triggers, and graduated handoff is a real competitive differentiator.

3. **Task classification works.** Rule-based classifier with 10 task types, ML fallback, 60-second cache. Handles cold-start correctly with routing_policy.yaml defaults.

4. **Atlas model personas are well-defined.** Each of the 7 models has domain prompts, provider preferences, and local fallback models, all configured in providers.yaml.

5. **Dashboard provides real operational visibility.** 30+ admin endpoints covering analytics, conversations, routing decisions, training status, model performance, and live WebSocket feed.

6. **PII masking is enterprise-grade.** Detects 10 PII types with reversible masking. Applied before external provider calls.

7. **Session memory is well-designed.** LRU in-memory cache with Redis persistence fallback. Resolves by session_id or previous_response_id.

---

## Biggest Concerns

### Critical

1. **Zero multi-tenant isolation.** Any API key can access any workspace's agents, applications, conversations, and usage data. No RBAC. No workspace-scoped queries enforced.

2. **Hardcoded database credentials.** Default connection string contains plaintext password in source code and .env.example.

3. **No CI/CD pipeline.** No automated testing, no deployment automation, no quality gates.

4. **Test coverage is ~5%.** Only classifier, PII masker, and similarity scoring have tests. No integration, provider, DB, or API tests.

### High

5. **Three routers duplicate core logic.** Distillation retry, session loading, PII masking, and metric recording are implemented independently in each router with inconsistencies (atlas_router has no metrics).

6. **Provider error handling is inconsistent.** Some providers swallow exceptions, some propagate. Error response shapes differ across endpoints. Rate limit fails open when Redis is down.

7. **No distributed tracing.** OpenTelemetry is optional and off by default. No request-id threading through the call stack. Cannot correlate user request to provider call.

8. **Docker image includes dev dependencies.** Production container installs pytest/ruff. No health check instruction. No init process.

### Medium

9. **Agent/planner/notebook/computer-use are prototype quality.** Structurally present but lack tests, error handling, security boundaries, and production hardening.

10. **Database schema has gaps.** Conversation and UsageRecord lack workspace_id. RoutingDecision has dangling FK on account deletion. No migration for workspace isolation.

---

## Concise Architecture Summary

```
Client (OpenClaw / Paperclip / Dashboard)
    |
    v
FastAPI Application (app.py)
    |
    +-- /atlas           --> atlas_router.py    --> classify + distill + respond
    +-- /v1/chat/comp    --> openai_router.py   --> route + respond
    +-- /v1/responses    --> responses_router.py --> normalize + route + respond
    +-- /admin/*         --> dashboard/router.py --> analytics + CRUD
    +-- /ws/chat/*       --> chat/ws.py          --> WebSocket rooms
    +-- /notebooks/*     --> notebook/router.py  --> cell execution
    |
    v
Provider Registry (registry.py)
    |
    +-- Claude CLI (local)
    +-- Ollama (2 GPU servers, 7 models)
    +-- Anthropic API
    +-- OpenAI / Groq / Vertex / LiteLLM / Moonshot
    |
    v
Distillation Pipeline (auto_trainer.py)
    |
    +-- Dual execution (teacher + student)
    +-- Similarity scoring (ROUGE-L, Jaccard)
    +-- Sample collection (DualExecutionRecord)
    +-- Training trigger (QLoRA via Unsloth)
    +-- Graduated handoff (0% --> 90% local traffic)
    |
    v
Database (SQLite dev / PostgreSQL prod)
    +-- 20+ tables (conversations, routing_decisions, training_runs, agents, etc.)
    +-- Alembic migrations (7 versions)
```

**Key architectural fact:** There is no control plane / data plane separation. The same FastAPI process handles API requests, admin operations, training orchestration, and WebSocket connections. This is acceptable for current scale but will need splitting for production enterprise deployment.
