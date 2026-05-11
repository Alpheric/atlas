# 05 - Data and API Review

---

## Database/Schema Review

**Engine:** SQLAlchemy 2.x async with SQLite (dev) / PostgreSQL (prod)
**ORM style:** Mapped columns with type annotations
**Migration tool:** Alembic with 7 versions

### Entity Inventory (20+ tables)

| Table | Purpose | Workspace Scoped |
|-------|---------|-----------------|
| conversations | Chat sessions | No |
| messages | Individual messages | No (via conversation) |
| routing_decisions | Per-message routing audit | No |
| quality_signals | Human/auto quality scores | No |
| model_performance | Aggregated model stats | No |
| dual_execution_records | Teacher/student pairs | No |
| task_type_readiness | Per-task training state | No |
| training_runs | QLoRA job tracking | No |
| api_keys | Client authentication | No |
| provider_accounts | Encrypted provider keys | No |
| usage_records | Per-request cost/tokens | No |
| usage_hourly_rollups | Pre-aggregated hourly stats | No |
| workspaces | Organizational units | (is the entity) |
| teams | Groups within workspace | Yes |
| channels | Chat channels within team | Yes (via team) |
| channel_members | User-channel membership | Yes (via channel) |
| applications | Packaged AI deployments | Yes |
| agents | Named AI agents | Yes |
| agent_messages | Agent-to-agent queue | Yes (via agent) |
| agent_executions | Agent audit log | Yes (via agent) |
| task_plans | Multi-step plans | Yes |
| computer_sessions | Browser/desktop audit | Yes |
| notebooks | AI notebooks | Yes |
| notebook_cells | Notebook cells | Yes (via notebook) |

### Entity Consistency Issues

**CRITICAL: Split-era schema**
The first 12 tables (conversations through usage_hourly_rollups) were designed before multi-tenancy. The last 12 (workspaces through notebook_cells) were designed with it. This creates a fundamental inconsistency where core operational data cannot be attributed to a workspace.

**HIGH: Missing FK on RoutingDecision.account_id**
```python
account_id: Mapped[uuid.UUID | None] = mapped_column(
    UUID(as_uuid=True), ForeignKey("provider_accounts.id"), nullable=True
)
```
No ondelete action specified. If ProviderAccount is deleted, this becomes a dangling reference. Should be `ondelete="SET NULL"`.

**MEDIUM: Excessive nullable fields**
Many fields are nullable where a default would be more appropriate:
- `Agent.system_prompt` (should default to empty string)
- `Application.system_prompt` (same)
- `TrainingRun.started_at` (null until started, acceptable)
- `TaskTypeReadiness.best_local_model` (null until first training, acceptable)

---

## Model Registry Readiness

**Current:** No model registry table exists.

**What exists:**
- TaskTypeReadiness tracks per-task-type training state and handoff percentage
- TrainingRun tracks individual QLoRA jobs with base_model, config, metrics, artifact_path
- providers.yaml defines external model specs

**What is missing:**
- A `model_versions` table tracking: version_id, base_model, adapter_path, training_run_id, evaluation_scores, status (draft/staging/active/retired), created_at, activated_at, retired_at
- A `model_deployments` table tracking: model_version_id, target_server, deployment_status, deployed_at
- Version comparison and rollback support

---

## Release/Version Readiness

**Current:** No release or versioning system exists.

- No concept of a "release" for Atlas configuration changes
- No versioned API (though /v1/ prefix follows OpenAI convention)
- No model version tracking beyond TrainingRun records
- No deployment promotion workflow (dev -> staging -> prod)
- No configuration version history

---

## API Consistency

### Entry Points

| Endpoint | Format | Auth | Response Shape |
|----------|--------|------|----------------|
| POST /atlas | Atlas-native dict | API key | Responses API JSON |
| POST /v1/chat/completions | OpenAI ChatCompletion | API key | ChatCompletionResponse |
| POST /v1/responses | OpenAI Responses API | API key | Custom dict |
| GET /admin/* | REST | API key | `{"data": [...]}` |
| POST /admin/* | REST | API key | `{"id": "...", "status": "..."}` |
| WS /ws/chat/* | WebSocket JSON | Token query param | `{"type": "message", ...}` |
| GET /notebooks | REST | API key | `{"data": [...]}` |
| POST /notebooks/*/cells/*/run | REST | API key | Custom dict |

**Issues:**
1. Error responses differ across endpoints (no standard error model)
2. Admin endpoints return `{"data": [...]}` but proxy endpoints return direct objects
3. No pagination standard (some endpoints have limit/offset, most do not)
4. No ETag or caching headers on admin endpoints
5. WebSocket auth uses query parameter token (acceptable for WS, not ideal)

### Missing Standard Headers
- No `X-Request-Id` header propagation
- No `X-RateLimit-*` headers in responses
- No `Retry-After` on rate limit errors

---

## Missing Domain Entities

| Entity | Purpose | Priority |
|--------|---------|----------|
| User | Identity beyond API key | Critical |
| Role | Permission sets | Critical |
| ModelVersion | Trained model tracking | High |
| ModelDeployment | Deployment target tracking | High |
| Policy | Routing/cost/access rules | High |
| AuditEvent | Admin action logging | High |
| Webhook | Outbound event notifications | Medium |
| Region | Geographic deployment unit | Low |
| Budget | Per-tenant cost limits | Medium |
| ReleaseConfig | Versioned configuration snapshots | Low |

---

## Contract Quality

**Pydantic models:** `request_models.py` and `response_models.py` define typed contracts for the OpenAI-compatible endpoints. These are well-structured.

**Admin endpoints:** Use raw `dict` input (no Pydantic validation). Missing field validation is done with manual checks:
```python
required = {"workspace_id", "name", "display_name"}
missing = required - body.keys()
if missing:
    raise HTTPException(400, f"Missing required fields: {missing}")
```
This works but is fragile. Should use Pydantic models for admin endpoints too.

**WebSocket messages:** No schema validation. JSON parsed with try/except but fields not validated.

---

## Eventing/Job Design

**ARQ (Redis-based job queue):**
- Used for: training job dispatch, periodic tasks
- Issue: Redis not running means training jobs silently fail to enqueue
- Issue: No dead letter queue, no retry policy visible
- Issue: Worker defined in docker-compose but no standalone worker entrypoint

**Background tasks (asyncio.create_task):**
- Used for: usage persistence, distillation comparison, agent execution audit
- Risk: If the main process crashes, in-flight background tasks are lost
- Risk: No concurrency limits on background tasks

**No event bus:**
- No pub/sub for system events (model graduated, training completed, error spike)
- No webhook dispatch for external integrations
- Dashboard WebSocket live feed is the only real-time output
