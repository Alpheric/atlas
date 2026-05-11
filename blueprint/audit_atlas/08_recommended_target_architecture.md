# 08 - Recommended Target Architecture

---

## Design Principles

1. **Middleware first.** Atlas is a routing and orchestration layer. It is not the AI model. Keep the execution plane thin and the control plane rich.
2. **Preserve what works.** The provider abstraction, routing classifier, distillation pipeline, and dashboard are real assets. Do not rewrite them.
3. **Fix foundations before adding features.** Multi-tenancy, auth, and pipeline unification must come before agents, notebooks, or computer use.
4. **Separate concerns cleanly.** Control plane (config, policy, admin) from data plane (request execution). Admin API from proxy API.
5. **Make it testable.** The CorePipeline must be unit-testable without hitting real providers.

---

## Recommended Target Architecture

```
                    +---------------------------+
                    |      Load Balancer        |
                    +---------------------------+
                              |
              +---------------+---------------+
              |                               |
     +--------v---------+          +----------v---------+
     |   Atlas API       |          |   Atlas Admin API   |
     |   (Data Plane)    |          |   (Control Plane)   |
     |                   |          |                     |
     | POST /atlas       |          | GET /admin/*        |
     | POST /v1/chat/*   |          | POST /admin/*       |
     | POST /v1/resp/*   |          | WS /admin/ws/*      |
     +--------+----------+          +----------+----------+
              |                                |
     +--------v----------+          +----------v----------+
     |  CorePipeline      |          |  Admin Services     |
     |  (unified flow)    |          |  - AgentService     |
     |                    |          |  - AppService       |
     |  1. Auth + Tenant  |          |  - WorkspaceService |
     |  2. Rate Limit     |          |  - TrainingService  |
     |  3. Session Load   |          |  - PolicyService    |
     |  4. PII Mask       |          |  - AuditService     |
     |  5. Classify       |          +---------------------+
     |  6. Route/Policy   |
     |  7. Execute        |          +---------------------+
     |  8. PII Unmask     |          |  Background Workers |
     |  9. Cache Store    |          |  - ARQ: training    |
     | 10. Session Save   |          |  - ARQ: evaluation  |
     | 11. Audit Record   |          |  - ARQ: deployment  |
     | 12. Metrics        |          |  - Cron: health     |
     +--------+-----------+          +---------------------+
              |
     +--------v-----------+
     |  Provider Registry  |
     |  + Routing Engine   |
     +--------+------------+
              |
    +---------+---------+---------+---------+
    |         |         |         |         |
  Claude   Ollama    OpenAI    Vertex   Moonshot
  (CLI/API) (local)  (API)     (API)    (API)
              |
     +--------v-----------+
     |  Distillation Eng.  |
     |  - Dual execution   |
     |  - Similarity score |
     |  - Sample collect   |
     |  - Training trigger |
     |  - Lifecycle mgmt   |
     +---------------------+
              |
     +--------v-----------+
     |  Database Layer     |
     |  PostgreSQL + Redis |
     +---------------------+
```

---

## What Should Remain (Preserve)

| Component | Why |
|-----------|-----|
| Provider base.py + registry.py | Clean abstraction, well-designed |
| 8 provider implementations | Working, tested in production |
| routing/classifier.py + strategy.py + scorer.py | Effective, well-tested |
| providers.yaml + routing_policy.yaml | Good config pattern |
| session/manager.py | Well-encapsulated, Redis-ready |
| security/pii_masker.py | Enterprise-grade, well-tested |
| feedback/argilla_sync.py | Valuable for model governance |
| training/ module structure | Novel differentiator |
| dashboard-ui/ | Functional, covers all major views |
| Pydantic request/response models | Clean contracts |
| Alembic migration chain | Good versioning discipline |

---

## What Should Be Redesigned

### 1. CorePipeline (replace 3 separate router implementations)
**Current:** Three routers each implement the full execution path independently.
**Target:** A single `CorePipeline` class in `proxy/pipeline.py` that handles steps 1-12 above. Routers become thin adapters that normalize input/output format and call `CorePipeline.execute()`.

### 2. Auth + Tenant Middleware
**Current:** API key string comparison in auth.py. No tenant binding.
**Target:** Auth middleware that resolves API key to (user_id, workspace_id, roles). All downstream operations scoped to workspace. Rate limiting separate from auth.

### 3. Admin Router Split
**Current:** 1219-line god-file.
**Target:** Split into:
- `admin/analytics_router.py`
- `admin/agents_router.py`
- `admin/applications_router.py`
- `admin/workspaces_router.py`
- `admin/training_router.py`
- `admin/plans_router.py`

### 4. auto_trainer.py Split
**Current:** 924-line file with 8+ responsibilities.
**Target:** Split into:
- `distillation/executor.py` (dual execution + streaming)
- `distillation/lifecycle.py` (state machine + handoff)
- `distillation/scoring.py` (similarity + quality)
- `distillation/triggers.py` (training triggers + throttling)

---

## What Should Be Split

| Current | Recommended Split |
|---------|-------------------|
| app.py lifespan (100+ LOC) | startup/lifespan.py for initialization sequence |
| dashboard/router.py (1219 LOC) | 6 sub-routers |
| auto_trainer.py (924 LOC) | 4 focused modules |
| common/auth.py (auth + rate limit) | auth/authenticator.py + auth/rate_limiter.py |

---

## What Should Be Centralized

| Concern | Current Location | Centralize To |
|---------|------------------|---------------|
| Error response formatting | Each router | proxy/errors.py with standard model |
| Request ID generation | Each router | Middleware in app.py |
| Workspace scoping | Optional query params | Auth middleware |
| Provider selection for Atlas | auto_trainer._get_external_provider | routing/atlas_router_strategy.py |
| Lifecycle state transitions | auto_trainer._transition_lifecycle | distillation/lifecycle.py |

---

## What Should Be Regionalized (Future)

Not needed now. When needed:
- Provider registry gains region metadata
- Routing engine gains region-aware scoring
- Database gains region affinity for data residency
- Deploy separate Atlas instances per region with shared control plane

---

## Best Next Architecture From Here

**Phase approach:** Stabilize the middleware core, then harden for enterprise, then extend for model family.

1. **Unify the pipeline** (1-2 weeks). Build CorePipeline. Make routers thin. Fix metrics gap. Standardize errors.

2. **Add auth and tenancy** (2-3 weeks). Auth middleware with workspace binding. Scope all queries. Add user/role tables. Migrate existing data.

3. **Split god-files** (1 week). Dashboard router, auto_trainer, app.py lifespan.

4. **Add CI/CD and tests** (2 weeks). GitHub Actions for lint/test/build. Integration tests for CorePipeline. Provider mock tests.

5. **Harden for production** (2-3 weeks). Fix Docker image. Add health probes. Enable OTEL by default. Add structured JSON logging. Add Prometheus endpoint.

6. **Model registry and versioning** (2-3 weeks). Model version table. Deployment tracking. Approval workflow. Rollback support.

7. **Agent and planning hardening** (2-3 weeks). Tests, error handling, workspace scoping, execution limits.

Total: ~12-16 weeks to enterprise-ready Atlas middleware.
