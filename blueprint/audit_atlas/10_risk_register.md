# 10 - Risk Register

---

## Product Risks

| ID | Risk | Likelihood | Impact | Mitigation |
|----|------|------------|--------|------------|
| P1 | Atlas perceived as toy/prototype due to missing enterprise features | High | High | Prioritize auth/tenancy before new features |
| P2 | Distillation pipeline quality degrades silently (no alerting) | Medium | High | Add quality monitoring + alerts on graduated model regression |
| P3 | Agent/notebook/computer-use features are half-built, creating confusion about product scope | Medium | Medium | Clearly gate as "experimental" or defer entirely |
| P4 | Dashboard UI does not reflect multi-tenant reality | Medium | Medium | Scope dashboard to workspace context after auth work |

---

## Security Risks

| ID | Risk | Likelihood | Impact | Mitigation |
|----|------|------------|--------|------------|
| S1 | Cross-tenant data access via API | High (if multi-tenant deployed) | Critical | Enforce workspace_id on all queries immediately |
| S2 | Hardcoded database credentials in source code | Certain (exists today) | High | Remove defaults, require env vars |
| S3 | Rate limiting fails open when Redis is down | Medium | High | Add in-memory fallback rate limiter (token bucket) |
| S4 | Notebook execution runs arbitrary code without sandboxing | High (if notebook enabled) | Critical | Defer notebook or add containerized sandbox |
| S5 | Agent persona hijacking (any key can use any agent_id) | Medium | Medium | Scope agent access to workspace |
| S6 | PII masking bypassed on agent injection path | Low | Medium | Move agent injection after PII masking in atlas_router |
| S7 | API keys stored in plaintext in settings.api_keys | Medium | High | Hash keys at rest, compare hashes |

---

## Architecture Risks

| ID | Risk | Likelihood | Impact | Mitigation |
|----|------|------------|--------|------------|
| A1 | Three-router duplication leads to divergent behavior | Certain (exists today) | High | CorePipeline extraction (30-day plan) |
| A2 | Monolithic process blocks scaling | Medium | High | Separate control plane from data plane when scaling is needed |
| A3 | In-memory metrics/sessions lost on restart | Certain | Medium | Move to Redis-backed metrics. Sessions already have Redis path. |
| A4 | auto_trainer.py god-file becomes unmaintainable | High | Medium | Split into 4 modules (60-day plan) |
| A5 | No separation between proxy and admin auth scopes | Medium | Medium | Separate API key types (proxy key vs admin key) |

---

## Operational Risks

| ID | Risk | Likelihood | Impact | Mitigation |
|----|------|------------|--------|------------|
| O1 | No CI/CD means regressions ship silently | High | High | Set up GitHub Actions (60-day plan) |
| O2 | No alerting means outages go unnoticed | High | High | Add basic health monitoring + Slack/email alerts |
| O3 | Redis dependency without fallback for training dispatch | Certain | Medium | Log warning + skip dispatch. Already partially handled. |
| O4 | Single-server deployment (no HA) | Certain | High | Plan for multi-instance with shared state (Redis) |
| O5 | No backup strategy for database | High | Critical | Add pg_dump cron job or managed DB service |

---

## Scaling Risks

| ID | Risk | Likelihood | Impact | Mitigation |
|----|------|------------|--------|------------|
| SC1 | In-memory session/metrics prevent horizontal scaling | Blocked | High | Redis-backed session (path exists), Redis-backed metrics |
| SC2 | Single ARQ worker limits training throughput | Low (current scale) | Medium | Add worker pool configuration |
| SC3 | WebSocket rooms in-memory, cannot scale across instances | Low (current scale) | Medium | Redis pub/sub for room state |
| SC4 | SQLite dev DB limits concurrent writes | Certain (dev) | Low | Postgres in production (path exists) |

---

## Governance Risks

| ID | Risk | Likelihood | Impact | Mitigation |
|----|------|------------|--------|------------|
| G1 | No audit trail for admin actions (agent CRUD, training triggers) | Certain | High | Add AuditEvent table + logging middleware |
| G2 | Model promotion is automatic (no human approval gate) | Certain | Medium | Add approval workflow for canary -> graduated |
| G3 | No cost governance (unbounded spend on external providers) | Medium | High | Add per-workspace budget table + enforcement |
| G4 | Training data not reviewed before model training | Medium | Medium | Argilla gate exists but optional. Make it default for production. |

---

## Delivery Risks

| ID | Risk | Likelihood | Impact | Mitigation |
|----|------|------------|--------|------------|
| D1 | Low test coverage (5%) makes refactoring risky | High | High | Write CorePipeline tests before extracting it |
| D2 | Feature sprawl (agents, notebooks, tools, chat) dilutes focus | Medium | High | Defer P4 features, focus on middleware hardening |
| D3 | Single developer (bus factor = 1) | High | Critical | Document architecture, add CLAUDE.md maintenance |
| D4 | No staging environment means production is the test | High | High | Add docker-compose.staging.yml or separate deploy target |

---

## Top 5 Risks by Combined Score

| Rank | ID | Risk | Score (L*I) |
|------|-----|------|-------------|
| 1 | S1 | Cross-tenant data access | 5*5 = 25 |
| 2 | D3 | Single developer bus factor | 5*5 = 25 |
| 3 | O1 | No CI/CD, silent regressions | 5*4 = 20 |
| 4 | A1 | Three-router divergent behavior | 5*4 = 20 |
| 5 | S2 | Hardcoded database credentials | 5*4 = 20 |
