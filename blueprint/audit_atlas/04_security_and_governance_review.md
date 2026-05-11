# 04 - Security and Governance Review

---

## Auth Review

**Current implementation:** `src/a1/common/auth.py` (96 lines)

| Aspect | Status | Detail |
|--------|--------|--------|
| Authentication method | API key (Bearer token) | Simple string comparison against settings.api_keys list |
| User identity | None | API key hash used for rate limiting, not identity |
| Session management | None | Stateless key verification per request |
| Multi-factor | None | Not applicable for API-based auth |
| Token expiry | None | API keys never expire |
| Dev mode bypass | Yes | If settings.api_keys is empty, all requests pass (auth.py line 82-84) |

**Risk:** Any valid API key has full access to all resources, all workspaces, all admin operations. No concept of user, role, or scope.

**Severity:** CRITICAL

---

## Permission Review

**Current state:** No permission system exists.

- No roles (admin, developer, viewer, auditor)
- No resource-level permissions (who can create agents, who can trigger training, who can view conversations)
- No scope restrictions on API keys
- Dashboard admin endpoints have same auth as proxy endpoints
- Agent creation, workspace creation, training triggers all require only a valid API key

**Severity:** CRITICAL

---

## Tenancy Review

**Current state:** Multi-tenant schema exists but is not enforced.

| Table | Has workspace_id | Enforced in queries |
|-------|-------------------|---------------------|
| Workspace | (is the entity) | N/A |
| Team | Yes | No -- optional filter |
| Channel | Via Team FK | No |
| Agent | Yes | No -- optional filter |
| Application | Yes | No -- optional filter |
| Conversation | No | N/A -- no workspace concept |
| Message | No | N/A |
| RoutingDecision | No | N/A |
| UsageRecord | No | N/A |
| DualExecutionRecord | No | N/A |
| TrainingRun | No | N/A |
| TaskPlan | Yes | No -- optional filter |

**Finding:** The organizational schema (workspaces, teams, channels) was added in P1/P2 but the core data model (conversations, routing, usage, training) predates it and has no workspace binding. Cross-tenant data access is possible and easy.

**Severity:** CRITICAL

---

## Secrets/Config Review

| Secret/Config | Storage | Risk |
|---------------|---------|------|
| Database password | Hardcoded default in settings.py line 14 | CRITICAL: plaintext in source |
| API keys (client) | settings.api_keys list from .env | HIGH: plaintext in memory |
| Provider API keys | key_pool.py with Fernet encryption | Good when encryption_key set |
| Encryption key | A1_ENCRYPTION_KEY env var | Good, but empty default means plaintext fallback |
| Argilla API key | settings.argilla_api_key from .env | MEDIUM: plaintext in memory |
| Moonshot API key | settings.moonshot_api_key from .env | MEDIUM: plaintext in memory |
| Redis URL | settings.redis_url from .env | LOW: typically localhost |

**Positive:** app.py (line 48-59) refuses to start if provider accounts exist without an encryption key. This is a good security gate.

**Negative:** Default database_url contains plaintext credentials. If a developer copies settings.py defaults into production, the password is exposed.

**Severity:** CRITICAL (hardcoded defaults), MEDIUM (runtime memory exposure)

---

## Audit Trail Review

| Audit Capability | Status | Detail |
|-----------------|--------|--------|
| Request logging | Partial | RequestLogMiddleware logs method/path/status. No request body or response body. |
| Routing decisions | Yes | RoutingDecision table records provider, model, latency, cost per message |
| Usage tracking | Yes | UsageRecord table per request |
| Training decisions | Yes | TrainingRun table with status, config, metrics |
| Agent executions | Yes | AgentExecution table with task, result, latency, cost |
| Admin actions | No | No audit log for CRUD operations (create workspace, create agent, etc.) |
| Auth events | No | No log of failed auth attempts, rate limit hits |
| Config changes | No | No change tracking on settings or policies |

**Severity:** HIGH (no admin action audit)

---

## Model/Provider Security Boundaries

| Boundary | Status |
|----------|--------|
| PII masked before external calls | Yes, but inconsistent across routers |
| Agent prompts masked before external calls | No -- agent system_prompt injected before PII masking in atlas_router |
| Tool definitions stripped for Claude CLI | Yes (claude_cli.py:_strip_tool_definitions) |
| Provider credentials isolated | Yes (key_pool.py with encryption) |
| Local model outputs not sent externally | Yes -- student responses stay local |
| Training data not sent externally | Yes -- DualExecutionRecords stored locally |
| Response caching PII-safe | Yes -- cache stores after unmasking |

**Severity:** MEDIUM (agent prompt injection path bypasses PII masking)

---

## Prompt Injection and Tool Safety Review

| Risk Area | Status | Detail |
|-----------|--------|--------|
| System prompt injection | Partial mitigation | Atlas identity injected by provider (atlas_router, claude_cli). But user can override via input messages. |
| Tool-use safety | Weak | tools/computer.py executes browser/desktop actions with no sandboxing when enabled |
| Notebook execution | Dangerous | kernel.py runs arbitrary Python in subprocess with 30s timeout but no resource limits, no sandboxing, no filesystem isolation |
| Agent persona override | Possible | Any API key holder can specify agent_id and hijack the agent's persona for their request |
| OpenClaw message injection | Partial mitigation | responses_router.py strips heartbeats and deduplicates, but does not validate message content |

**Severity:** HIGH (notebook execution), MEDIUM (tool safety)

---

## External Model Usage Controls

| Control | Status |
|---------|--------|
| Which providers a tenant can use | Not implemented |
| Cost caps per request | Not implemented |
| Cost caps per tenant | Not implemented |
| Content filtering before external send | PII masking only, no content policy |
| Response filtering from external | None |
| Provider failover policies | Implicit via preference chain, not configurable |
| Model access restrictions | None -- all models available to all keys |

**Severity:** HIGH

---

## Governance Maturity Rating

| Governance Area | Rating (1-5) | Notes |
|-----------------|--------------|-------|
| Identity and access | 1 | API key only, no users, no roles |
| Data isolation | 1 | No workspace enforcement |
| Audit and compliance | 2 | RoutingDecision + UsageRecord exist but incomplete |
| Policy management | 1 | No policy abstractions |
| Change management | 1 | No approval workflows, no release gates |
| Incident response | 1 | No alerting, no runbooks |
| Cost governance | 2 | Cost tracking exists, no enforcement |
| Model governance | 2 | Lifecycle state machine exists, no approval gates |

**Overall Governance Maturity: 1.4 / 5 (Ad-hoc)**

The system has data collection for governance (routing decisions, usage, training runs) but lacks enforcement, policies, and workflows.
