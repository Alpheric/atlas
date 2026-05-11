# 03 - Feature Gap Analysis

---

## Critical Gaps

### 1. Multi-Tenant Workspace Isolation
**What exists:** Workspace, Team, Channel tables. Optional workspace_id query parameters on admin endpoints.
**What is missing:** Enforcement. Any API key can access any workspace's data. No middleware to bind API key to workspace. Conversation and UsageRecord tables lack workspace_id entirely.
**Impact:** Atlas cannot serve multiple teams or customers without data leakage.

### 2. Role-Based Access Control
**What exists:** API key verification. Rate limiting per key.
**What is missing:** Roles (admin, developer, viewer). Permissions per resource type. Scope restrictions per key. No concept of "user" beyond api_key_hash.
**Impact:** Cannot restrict dashboard access, cannot limit agent creation to admins, cannot give read-only access to analysts.

### 3. Unified Request Pipeline
**What exists:** Three separate routers (openai, atlas, responses) each implementing their own version of: session load, PII mask, classify, route, execute, unmask, save, record metrics.
**What is missing:** A single CorePipeline that all three routers call. pipeline.py has helpers but is not the execution backbone.
**Impact:** Bugs fixed in one router are not fixed in others. atlas_router has no metrics. Error shapes differ. Maintenance cost multiplied by 3.

### 4. CI/CD Pipeline
**What exists:** Nothing. No GitHub Actions, no pre-commit hooks, no automated testing.
**What is missing:** Lint on push, test on PR, build on merge, deploy on release.
**Impact:** No quality gates. Regressions can ship silently. No reproducible builds.

---

## High Gaps

### 5. Distributed Tracing
**What exists:** OpenTelemetry setup (optional, off by default). FastAPI auto-instrumentation.
**What is missing:** Request-id propagation through the full stack. Custom spans for business logic (classification, caching, PII masking, provider call). Correlation of user request to provider invocation.
**Impact:** Cannot debug production issues. Cannot measure end-to-end latency per component.

### 6. Model Registry and Versioning
**What exists:** Lifecycle state machine on TaskTypeReadiness. TrainingRun records.
**What is missing:** A model registry table tracking: adapter version, base model, training dataset hash, evaluation scores, approval status, deployment target, active/inactive flag. No rollback path.
**Impact:** Cannot track which model version is serving traffic. Cannot compare versions. Cannot safely roll back a bad deployment.

### 7. Policy Engine
**What exists:** Routing by task type + provider preference chain.
**What is missing:** Configurable policies for: cost limits per request/tenant, latency SLAs, provider restrictions per tenant, content filtering rules, model access controls.
**Impact:** Cannot enforce organizational rules about AI usage. Cannot implement "team X may only use local models."

### 8. Error Standardization
**What exists:** Each router returns errors in different JSON shapes.
**What is missing:** A standard error response model used across all endpoints. Error codes. Error categorization (provider error, validation error, auth error, internal error).
**Impact:** Client applications cannot reliably parse errors from Atlas.

### 9. Test Coverage
**What exists:** 4 test files (~50 tests) covering classifier, PII masker, similarity scoring, and basic smoke tests.
**What is missing:** Integration tests for the full request pipeline. Provider mock tests. Database tests. Dashboard API tests. WebSocket tests. Training pipeline tests. Agent executor tests.
**Impact:** Cannot refactor safely. Cannot verify correctness after changes. Estimated coverage: ~5%.

---

## Medium Gaps

### 10. Request Audit Trail
**What exists:** RoutingDecision table (per message). UsageRecord (per request). In-memory metrics.
**What is missing:** Full request audit (who, when, what model, what cost, what response status, what provider, what latency, what errors). Immutable audit log. Audit retention policy.
**Impact:** Cannot demonstrate compliance. Cannot investigate incidents.

### 11. Cost Controls
**What exists:** Cost estimation in ModelInfo. Usage tracking in UsageRecord.
**What is missing:** Per-tenant budget limits. Per-request cost caps. Budget alerts. Cost attribution by workspace/team/application.
**Impact:** Uncontrolled spend. Cannot bill back to teams.

### 12. API Versioning
**What exists:** /v1/chat/completions and /v1/responses (follows OpenAI convention).
**What is missing:** Atlas-native API versioning. Deprecation headers. Breaking change management.
**Impact:** Cannot evolve the Atlas API without breaking existing clients.

### 13. Secret Rotation
**What exists:** key_pool.py with encrypted API key storage. Fernet encryption.
**What is missing:** Automatic key rotation. Expiry tracking. Rotation alerts.
**Impact:** Keys that should be rotated stay active indefinitely.

### 14. Dashboard Split
**What exists:** 1219-line dashboard/router.py with 30+ endpoints.
**What is missing:** Sub-routers for analytics, agents, applications, workspaces, training, plans. Proper separation of concerns.
**Impact:** Hard to maintain. Hard to add features. Hard to assign ownership.

---

## Low Gaps

### 15. Multi-Region Support
Not needed today. Single deployment. But the architecture has no region concept (no region field on providers, no geo-routing, no data residency controls).

### 16. SSO/OIDC Integration
Not needed for initial deployment. But blocks enterprise customer onboarding.

### 17. Webhook/Event System
No outbound event system. Cannot notify external systems when training completes, model graduates, or errors spike.

### 18. Agent-to-Agent Protocol
AgentMessage table exists but no execution loop that reads from it. Agents can only be invoked directly, not through message passing.

### 19. Notebook Production Hardening
Kernel.py executes arbitrary Python in a subprocess with 30-second timeout. No sandboxing. No resource limits. No execution isolation.

### 20. Computer Use Security
tools/computer.py is gated behind settings.computer_use_enabled (good). But when enabled, there are no workspace-level permissions, no action audit trail, no screenshot PII masking.
