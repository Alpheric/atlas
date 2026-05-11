# 02 - Architecture Verification

---

## Actual Implemented Architecture

Atlas is a **monolithic FastAPI application** that combines:
- API gateway (3 OpenAI-compatible endpoints)
- Provider orchestration (8 LLM backends)
- Distillation pipeline (teacher/student dual execution)
- Admin dashboard (30+ endpoints)
- Agent framework (registry, executor, planner)
- WebSocket chat server
- Notebook execution engine
- Background job dispatch (ARQ/Redis)

All of these run in a single Python process. There is no separation between control plane and data plane.

---

## Middleware-First Verification

**Question:** Is Atlas truly middleware-first today?

**Answer:** Partially. The proxy layer is genuinely middleware -- it normalizes requests, routes to providers, and returns responses. But the system conflates middleware concerns with application concerns (agents, chat, notebooks, planning).

| Middleware Concern | Status | Evidence |
|-------------------|--------|----------|
| Unified request entry | Partial | 3 separate routers, not unified |
| Provider abstraction | Strong | base.py + registry.py + 8 implementations |
| Request normalization | Partial | Each router normalizes differently |
| Routing engine | Strong | classifier.py + strategy.py + scoring |
| Policy engine | Missing | No policy enforcement layer |
| Prompt/context builder | Weak | Scattered across routers, no central builder |
| Tool orchestration | Prototype | tools/ exists but not integrated into pipeline |
| Audit trail | Weak | RoutingDecision table exists but no full request audit |
| Observability | Partial | Metrics in-memory, OTEL optional |
| Cost/latency awareness | Strong | ModelInfo has cost, scorer uses latency data |
| Tenant-aware routing | Missing | No workspace_id in routing path |
| Region-aware routing | Missing | No region concept anywhere |

**Verdict:** Atlas has middleware bones but is not middleware-first. It is application-first with middleware capabilities bolted on. The middleware layer needs extraction and hardening.

---

## Control Plane vs Data Plane Verification

**Control Plane (should manage configuration, policies, tenants):**
- Workspace/Team/Channel CRUD -- exists but no enforcement
- Agent/Application definitions -- exists but no policy gates
- Provider configuration -- YAML-driven, not runtime-manageable
- Routing policy -- YAML file, not API-driven
- Training triggers -- manual or sample-count based
- No approval workflows
- No release management

**Data Plane (should handle request execution):**
- Request routing -- functional
- Provider calls -- functional
- Response streaming -- functional
- PII masking -- functional
- Session memory -- functional
- Distillation -- functional

**Verdict:** There is no architectural separation. The same process handles both control and data plane operations. This is acceptable for current scale but blocks multi-region deployment and operational isolation.

---

## Provider Abstraction Verification

**Strengths:**
- Abstract base class with 5 required methods (complete, stream, health_check, supports_model, list_models)
- ModelInfo dataclass with capabilities (vision, computer_use, tier, latency_class)
- Registry pattern with health state tracking + unhealthy duration circuit breaker
- `get_provider_for_model()` with healthy-first preference
- `get_providers_supporting(capability)` for capability-based selection

**Weaknesses:**
- No rate limit awareness per provider
- No request-level cost budgets
- No provider-level timeout configuration (hardcoded per implementation)
- Error handling inconsistent across implementations (some swallow, some propagate)
- No provider metrics (calls, latency, error rate) at the registry level
- Key rotation (key_pool.py) is separate from provider lifecycle

**Verdict:** Good foundation. Needs rate limiting, budget controls, and consistent error handling to be enterprise-grade.

---

## Routing Design Verification

**Strengths:**
- 10 task types with rule-based classifier + ML fallback
- Strategy pattern with cold-start defaults + live scoring after 20 samples
- Epsilon-greedy exploration (10% random alternative selection)
- Atlas model resolution from task type
- Fallback chain with provider health awareness
- providers.yaml as single source of truth for provider preferences per Atlas model

**Weaknesses:**
- No routing policies beyond task type (no tenant/region/cost/latency policies)
- No A/B testing framework for routing experiments
- Classification cached 60 seconds but no invalidation path
- No request-level routing overrides (user cannot request specific provider)
- MODEL_PROVIDER_MAP was recently removed but `_get_provider_for()` still does linear search

**Verdict:** Solid for single-tenant. Needs policy engine and tenant awareness for enterprise.

---

## Model Lifecycle Readiness Verification

**Existing:**
- Lifecycle state machine: LEARNING -> TRAINING -> EVALUATING -> CANARY -> GRADUATED -> RETIRED
- TaskTypeReadiness table with sample counts, quality scores, handoff percentages
- DualExecutionRecord for teacher/student comparison data
- TrainingRun for QLoRA job tracking
- QualitySignal for human/auto evaluation
- Argilla integration for annotation approval gates
- lm-evaluation-harness integration for benchmark evaluation

**Missing:**
- No model version tracking (which adapter version is currently active?)
- No model registry (what models exist, what state are they in, who approved them?)
- No rollback mechanism (graduated model regresses, how to revert?)
- No adapter storage management (where are adapters stored, how are they deployed?)
- No A/B comparison between model versions
- No approval workflow for model promotion (canary -> graduated is automatic)

**Verdict:** The lifecycle concept is good. Implementation needs model versioning, registry, and approval workflows.

---

## Admin and Enterprise Readiness Verification

| Enterprise Requirement | Status | Detail |
|----------------------|--------|--------|
| RBAC | Missing | API key auth only, no roles |
| Multi-tenancy | Missing | Workspace tables exist, no isolation enforcement |
| Audit logging | Weak | RoutingDecision exists, no full request audit |
| Approval workflows | Missing | No human approval gates (except Argilla for training) |
| Release management | Missing | No model release process |
| Policy packs | Missing | No policy abstraction |
| Rate limiting | Partial | Per-key rate limit, fails open when Redis down |
| Cost controls | Missing | No per-tenant budget enforcement |
| SLA monitoring | Missing | No SLA definitions or alerting |
| Multi-region | Missing | No region concept |
| On-prem readiness | Partial | Docker setup exists, no air-gapped mode |
| SSO/OIDC | Missing | No external auth integration |
| API versioning | Missing | No /v2/ path, no deprecation headers |

**Verdict:** Atlas is a single-operator tool today. It needs foundational enterprise primitives before it can serve multiple teams or customers.
