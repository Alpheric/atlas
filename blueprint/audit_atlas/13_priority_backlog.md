# 13 - Priority Backlog

---

## Priority Levels
- **P0**: Must do immediately (blocks safety or correctness)
- **P1**: Must do within 30 days (blocks production readiness)
- **P2**: Must do within 60 days (blocks enterprise readiness)
- **P3**: Should do within 90 days (improves quality and capability)
- **P4**: Plan for later (nice-to-have or future requirement)

## Impact Levels
- **Critical**: System safety or data integrity
- **High**: Production reliability or core functionality
- **Medium**: Developer experience or feature completeness
- **Low**: Polish or optimization

## Complexity Levels
- **S**: Small (< 1 day)
- **M**: Medium (1-3 days)
- **L**: Large (3-7 days)
- **XL**: Extra large (1-2 weeks)

---

## Backlog

### 1. Remove hardcoded database credentials
**Why:** Plaintext password in source code is a critical security risk.
**Owner:** Security
**Priority:** P0 | **Impact:** Critical | **Complexity:** S
**Dependencies:** None
**Order:** 1

### 2. Add workspace_id to Conversation and UsageRecord tables
**Why:** Cannot attribute data to tenants. Blocks all multi-tenancy work.
**Owner:** Platform Backend
**Priority:** P0 | **Impact:** Critical | **Complexity:** M
**Dependencies:** None
**Order:** 2

### 3. Add metrics recording to atlas_router
**Why:** Distillation requests have zero observability. Blind spot in production monitoring.
**Owner:** Platform Backend
**Priority:** P0 | **Impact:** High | **Complexity:** S
**Dependencies:** None
**Order:** 3

### 4. Fix RoutingDecision FK cascade on account_id
**Why:** Dangling FK reference on ProviderAccount deletion.
**Owner:** Platform Backend
**Priority:** P0 | **Impact:** Medium | **Complexity:** S
**Dependencies:** None
**Order:** 4

### 5. Build CorePipeline -- unified execution path
**Why:** Eliminates triple duplication. Fixes inconsistencies. Makes all requests go through one tested path.
**Owner:** Platform Backend
**Priority:** P1 | **Impact:** Critical | **Complexity:** L
**Dependencies:** None
**Order:** 5

### 6. Standardize error response model
**Why:** Clients cannot reliably parse errors from Atlas. Three different error shapes.
**Owner:** Platform Backend
**Priority:** P1 | **Impact:** High | **Complexity:** M
**Dependencies:** #5 (easier to do during CorePipeline work)
**Order:** 6

### 7. Auth middleware with workspace binding
**Why:** No tenant isolation. Any API key accesses all data. Blocks enterprise deployment.
**Owner:** Security + Platform Backend
**Priority:** P1 | **Impact:** Critical | **Complexity:** L
**Dependencies:** #2 (workspace_id on tables)
**Order:** 7

### 8. Scope all repository queries to workspace
**Why:** Even with auth middleware, direct DB queries can leak data without workspace filters.
**Owner:** Platform Backend
**Priority:** P1 | **Impact:** Critical | **Complexity:** M
**Dependencies:** #2, #7
**Order:** 8

### 9. Add request ID propagation (X-Request-Id)
**Why:** Cannot trace a request through the system. Blocks production debugging.
**Owner:** Platform Backend
**Priority:** P1 | **Impact:** High | **Complexity:** S
**Dependencies:** #5 (add to CorePipeline)
**Order:** 9

### 10. Set up CI/CD pipeline (GitHub Actions)
**Why:** No automated quality gates. Regressions ship silently.
**Owner:** DevOps
**Priority:** P2 | **Impact:** High | **Complexity:** M
**Dependencies:** None
**Order:** 10

### 11. Split dashboard/router.py into sub-routers
**Why:** 1219-line god-file. Hard to maintain, hard to assign ownership.
**Owner:** Platform Backend
**Priority:** P2 | **Impact:** Medium | **Complexity:** M
**Dependencies:** None
**Order:** 11

### 12. Split auto_trainer.py into focused modules
**Why:** 924-line file with 8+ responsibilities. Hard to test and maintain.
**Owner:** AI Infrastructure
**Priority:** P2 | **Impact:** Medium | **Complexity:** M
**Dependencies:** None
**Order:** 12

### 13. Write CorePipeline integration tests
**Why:** Cannot safely refactor without tests. Current coverage ~5%.
**Owner:** Platform Backend
**Priority:** P2 | **Impact:** High | **Complexity:** L
**Dependencies:** #5
**Order:** 13

### 14. Write provider mock tests
**Why:** Provider failures are the most common production issue. Zero test coverage.
**Owner:** AI Infrastructure
**Priority:** P2 | **Impact:** High | **Complexity:** M
**Dependencies:** None
**Order:** 14

### 15. Harden Dockerfile for production
**Why:** Dev dependencies in prod image. No health check. No init process.
**Owner:** DevOps
**Priority:** P2 | **Impact:** High | **Complexity:** M
**Dependencies:** None
**Order:** 15

### 16. Enable OTEL by default with structured logging
**Why:** Cannot diagnose production issues without tracing and structured logs.
**Owner:** DevOps + Platform Backend
**Priority:** P2 | **Impact:** High | **Complexity:** M
**Dependencies:** #9 (request ID)
**Order:** 16

### 17. Add Prometheus /metrics endpoint
**Why:** In-memory metrics lost on restart. No Grafana integration path.
**Owner:** DevOps
**Priority:** P2 | **Impact:** Medium | **Complexity:** M
**Dependencies:** None
**Order:** 17

### 18. Add in-memory fallback rate limiter
**Why:** Rate limiting fails open when Redis is down.
**Owner:** Security
**Priority:** P2 | **Impact:** High | **Complexity:** S
**Dependencies:** None
**Order:** 18

### 19. Add Pydantic models for admin endpoint inputs
**Why:** Admin endpoints use raw dicts. Missing fields discovered at runtime.
**Owner:** Platform Backend
**Priority:** P2 | **Impact:** Medium | **Complexity:** M
**Dependencies:** #11 (easier during split)
**Order:** 19

### 20. Create model_versions and model_deployments tables
**Why:** Cannot track which model version is serving. Cannot roll back.
**Owner:** AI Infrastructure
**Priority:** P3 | **Impact:** High | **Complexity:** L
**Dependencies:** None
**Order:** 20

### 21. Add model promotion approval workflow
**Why:** canary -> graduated is automatic. No human gate for production models.
**Owner:** AI Infrastructure + Product
**Priority:** P3 | **Impact:** High | **Complexity:** M
**Dependencies:** #20
**Order:** 21

### 22. Add admin action audit logging
**Why:** No record of who created/deleted agents, triggered training, changed configs.
**Owner:** Security
**Priority:** P3 | **Impact:** High | **Complexity:** M
**Dependencies:** #7 (need user identity)
**Order:** 22

### 23. Add per-workspace cost budgets
**Why:** Unbounded external provider spend. Cannot bill back to teams.
**Owner:** Platform Backend
**Priority:** P3 | **Impact:** High | **Complexity:** M
**Dependencies:** #7, #8
**Order:** 23

### 24. Agent execution workspace scoping and limits
**Why:** Any API key can invoke any agent. No execution cost limits.
**Owner:** AI Infrastructure
**Priority:** P3 | **Impact:** Medium | **Complexity:** M
**Dependencies:** #7
**Order:** 24

### 25. Agent framework tests
**Why:** Zero test coverage on agents, executor, planner.
**Owner:** AI Infrastructure
**Priority:** P3 | **Impact:** Medium | **Complexity:** M
**Dependencies:** None
**Order:** 25

### 26. DB repository tests
**Why:** Zero test coverage on data access layer.
**Owner:** Platform Backend
**Priority:** P3 | **Impact:** Medium | **Complexity:** M
**Dependencies:** None
**Order:** 26

### 27. Add webhook/event system for external integrations
**Why:** Cannot notify external systems of model events, training completion, errors.
**Owner:** Platform Backend
**Priority:** P4 | **Impact:** Medium | **Complexity:** L
**Dependencies:** None
**Order:** 27

### 28. Notebook execution sandboxing
**Why:** Arbitrary Python execution with no isolation.
**Owner:** Security
**Priority:** P4 | **Impact:** High | **Complexity:** XL
**Dependencies:** Decision to keep notebook feature
**Order:** 28

### 29. SSO/OIDC integration
**Why:** Enterprise customers need SSO. API keys insufficient for large orgs.
**Owner:** Security
**Priority:** P4 | **Impact:** Medium | **Complexity:** L
**Dependencies:** #7 (auth middleware)
**Order:** 29

### 30. Multi-region provider routing
**Why:** Future requirement for geo-distributed deployment.
**Owner:** Architecture
**Priority:** P4 | **Impact:** Low (current) | **Complexity:** XL
**Dependencies:** Stable single-region production first
**Order:** 30

---

## Execution Summary

| Phase | Items | Timeline | Focus |
|-------|-------|----------|-------|
| Immediate (P0) | #1-4 | This week | Safety + correctness |
| 30-day (P1) | #5-9 | Weeks 2-4 | Core pipeline + auth |
| 60-day (P2) | #10-19 | Weeks 5-8 | Quality + production |
| 90-day (P3) | #20-26 | Weeks 9-12 | Governance + hardening |
| Future (P4) | #27-30 | 90+ days | Advanced features |
