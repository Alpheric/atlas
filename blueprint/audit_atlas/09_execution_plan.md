# 09 - Execution Plan

---

## Immediate Next Steps (This Week)

### 1. Fix hardcoded database credentials
**File:** config/settings.py line 14
**Action:** Remove default password from source. Set `database_url: str = ""` and validate at startup.
**Owner:** Security
**Effort:** 1 hour

### 2. Add workspace_id to Conversation and UsageRecord tables
**Files:** db/models.py, new alembic migration
**Action:** Add nullable workspace_id FK columns. Backfill from API key association where possible.
**Owner:** Platform Backend
**Effort:** 2 hours

### 3. Add metrics recording to atlas_router
**File:** proxy/atlas_router.py
**Action:** Add `metrics.record_request()` calls matching openai_router and responses_router.
**Owner:** Platform Backend
**Effort:** 30 minutes

### 4. Fix RoutingDecision FK cascade
**File:** db/models.py
**Action:** Add `ondelete="SET NULL"` to account_id FK.
**Owner:** Platform Backend
**Effort:** 30 minutes

---

## 30-Day Plan

### Week 1-2: CorePipeline Extraction

**Goal:** Single unified execution path for all 3 entry points.

1. Define `CorePipelineInput` and `CorePipelineResult` dataclasses
2. Implement `CorePipeline.execute()` with all 12 steps
3. Refactor `atlas_router.py` to use CorePipeline (most feature-complete, good first target)
4. Refactor `openai_router.py` to use CorePipeline
5. Refactor `responses_router.py` to use CorePipeline
6. Standardize error response model across all endpoints
7. Add integration tests for CorePipeline with mocked providers

**Owner:** Platform Backend
**Deliverable:** All requests flow through one path. Metrics, PII masking, sessions consistent.

### Week 3-4: Auth and Multi-Tenancy Foundation

**Goal:** API keys bound to workspaces. All queries workspace-scoped.

1. Add `User` and `Role` tables (or simple role field on ApiKey)
2. Add `workspace_id` to ApiKey table
3. Create auth middleware that resolves key to (workspace_id, roles)
4. Add `WorkspaceScope` dependency that injects workspace_id into all handlers
5. Update repositories to require workspace_id on all queries
6. Update admin endpoints to enforce workspace scoping
7. Separate rate limiting from auth (own middleware)

**Owner:** Security + Platform Backend
**Deliverable:** Tenant isolation enforced. No cross-workspace data access.

---

## 60-Day Plan

### Week 5-6: Code Quality and CI/CD

1. Split dashboard/router.py into 6 sub-routers
2. Split auto_trainer.py into 4 focused modules
3. Add Pydantic models for all admin endpoint inputs
4. Set up GitHub Actions: lint (ruff) + test (pytest) on every push
5. Add pre-commit hooks (ruff check, ruff format)
6. Write integration tests for CorePipeline (target: 30% coverage)
7. Write provider mock tests for at least claude_cli and ollama
8. Write DB repository tests

**Owner:** Platform Backend + DevOps
**Deliverable:** Automated quality gates. Safe to refactor.

### Week 7-8: Production Hardening

1. Fix Dockerfile: separate dev/prod installs, add health check, add tini
2. Remove hardcoded secrets from docker-compose (use .env exclusively)
3. Enable OTEL by default with trace ID in logs
4. Add structured JSON logging option
5. Add Prometheus /metrics endpoint
6. Add request ID propagation (X-Request-Id header)
7. Add graceful shutdown with request draining
8. Add startup health validation (all providers reachable or warn)

**Owner:** DevOps + Platform Backend
**Deliverable:** Production-deployable with observability.

---

## 90-Day Plan

### Week 9-10: Model Registry and Governance

1. Create `model_versions` table (version_id, base_model, adapter_path, training_run_id, eval_scores, status, timestamps)
2. Create `model_deployments` table (version_id, target_server, status)
3. Add approval workflow for model promotion (canary -> graduated requires admin approval)
4. Add model rollback capability (revert to previous active version)
5. Add model comparison endpoint (version A vs version B eval scores)
6. Wire model registry into distillation lifecycle

**Owner:** AI Infrastructure
**Deliverable:** Governed model lifecycle with audit trail.

### Week 11-12: Agent and Planning Hardening

1. Add workspace scoping to agent execution
2. Add execution limits (max tokens per agent turn, max total cost per plan)
3. Add agent execution tests
4. Add planning engine tests
5. Wire AgentMessage queue for agent-to-agent communication
6. Add plan execution progress WebSocket updates
7. Document agent API for external developers

**Owner:** AI Infrastructure
**Deliverable:** Production-ready agent framework.

---

## MVP Stabilization Priorities

1. CorePipeline unification
2. Auth and tenant isolation
3. Metrics gap fix (atlas_router)
4. Error standardization
5. CI/CD pipeline
6. Integration tests

---

## Enterprise Hardening Priorities

1. RBAC with workspace scoping
2. Audit logging for admin actions
3. Structured logging + OTEL
4. Prometheus metrics endpoint
5. Docker production hardening
6. Secret management (no hardcoded defaults)
7. API rate limiting that does not fail open

---

## Model-Family Preparation Priorities

1. Model registry and versioning
2. Model approval workflow
3. Model rollback capability
4. Evaluation framework integration (lm-eval harness already exists)
5. Adapter storage and deployment management
6. Atlas model-specific evaluation benchmarks

---

## What Should Be Explicitly Deferred

| Feature | Reason to Defer |
|---------|-----------------|
| Notebook execution | Prototype quality, security risks (arbitrary code execution), not core to middleware |
| Computer use tools | Gated feature, no demand signal, significant security surface |
| Multi-region | Premature without single-region production stability |
| SSO/OIDC | Can use API keys for initial enterprise customers |
| Webhook/event system | Nice-to-have after core stability |
| Agent-to-agent message protocol | Can use direct invocation for now |
| Advanced content filtering | PII masking covers the critical path |
| GraphQL API | REST is sufficient for current needs |
| Real-time collaboration on notebooks | Beyond current scope |
