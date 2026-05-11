# 07 - Operability Review

---

## Deployment Readiness

| Aspect | Status | Detail |
|--------|--------|--------|
| Container image | Exists | Dockerfile with Python 3.12 slim. But installs dev deps in prod. |
| Compose stack | Exists | docker-compose.yml for Postgres, Redis, Ollama, ARQ, Argilla, Jaeger |
| Production compose | Exists | docker-compose.prod.yml (overrides) |
| Health endpoint | Exists | GET /health returns {"status":"ok"} |
| Graceful shutdown | Partial | Lifespan cleanup closes Redis/ARQ. No drain for in-flight requests. |
| Init process | Missing | No tini or dumb-init for PID 1 signal handling |
| Resource limits | Missing | No memory/CPU limits in compose or Dockerfile |
| Secret injection | Weak | Hardcoded defaults in settings.py. Compose uses env vars inline. |
| Rolling updates | Missing | No health check in Dockerfile. No readiness probe. |

**Assessment:** Can deploy to a single server. Not ready for orchestrated deployment (Kubernetes, ECS).

---

## Observability

### Logging
- Structured logging via `common/logging.py` using Python logging module
- Per-module loggers (get_logger("module.name"))
- Request logging middleware logs method/path/status
- Quiets noisy libraries (httpx, httpcore, urllib3, ollama)
- **Gap:** No structured JSON logging. No request body/response body capture. No correlation IDs.

### Metrics
- In-memory metrics via `common/metrics.py`:
  - Request counter per provider/model/task_type
  - Token counter (prompt + completion)
  - Cost counter
  - Latency ring buffer (p50/p95/p99 per model)
  - Time-series (24h window, hourly granularity)
  - Error counter
  - Model leaderboard
- **Gap:** Metrics reset on restart (in-memory only). No Prometheus exporter endpoint. No Grafana-compatible output.

### Traces
- OpenTelemetry setup in `common/telemetry.py`
- FastAPI auto-instrumentation when enabled
- OTLP exporter to configured endpoint
- Jaeger available in docker-compose
- **Gap:** Disabled by default (settings.otlp_endpoint empty). No custom spans for business logic. No trace ID in logs.

### Alerting
- **Missing entirely.** No alert rules, no notification channels, no anomaly detection.

---

## Incident Readiness

| Capability | Status |
|-----------|--------|
| Error dashboards | Partial (in-memory metrics, dashboard live feed) |
| Alert on provider failure | Missing |
| Alert on error rate spike | Missing |
| Alert on latency degradation | Missing |
| Runbooks | Missing |
| On-call rotation | Missing |
| Postmortem templates | Missing |
| Health check for dependencies | Partial (provider health refresh every 60s) |

**Assessment:** The system can tell you something is wrong if you are watching the dashboard. It cannot proactively notify anyone.

---

## Config Handling

**Mechanism:** Pydantic BaseSettings with `A1_` env prefix, reading from .env file.

**Strengths:**
- Type-safe configuration with validation
- Environment variable override for all settings
- Separate .env, .env.example, .env.prod files

**Weaknesses:**
- No config validation beyond types (no range checks, no dependency validation)
- No config reload without restart
- No config versioning or change tracking
- providers.yaml and routing_policy.yaml loaded at startup only
- No feature flags system

---

## Environment Management

| Environment | Setup | Completeness |
|-------------|-------|-------------|
| Local dev | .env + SQLite + in-memory Redis fallback | Works |
| Docker dev | docker-compose.yml with Postgres/Redis | Works |
| Production | docker-compose.prod.yml + .env.prod | Partial -- hardcoded secrets |
| Staging | None | Missing |
| CI/CD | None | Missing |

**Gap:** No staging environment. No environment parity enforcement. No infrastructure-as-code beyond docker-compose.

---

## Scaling Readiness

| Dimension | Status | Blocker |
|-----------|--------|---------|
| Horizontal API scaling | Blocked | In-memory session cache and metrics not shared across instances |
| Database scaling | Ready | PostgreSQL with async driver. Would need read replicas for heavy analytics. |
| Provider scaling | Ready | Multiple providers with health-based routing |
| Training scaling | Blocked | Single ARQ worker, single GPU assumption |
| WebSocket scaling | Blocked | In-memory room state (_rooms dict) not shared |
| Cache scaling | Blocked | GPTCache uses local SQLite. TaskResponseCache is in-memory. |

**To scale horizontally:** Need Redis-backed session store (exists but Redis is offline), Redis-backed metrics, Redis pub/sub for WebSocket rooms, shared cache layer.

---

## Multi-Region Readiness

| Requirement | Status |
|-------------|--------|
| Region field on providers | Missing |
| Region-aware routing | Missing |
| Data residency controls | Missing |
| Cross-region replication | Missing |
| Region-specific endpoints | Missing |
| Geo-DNS | Missing |

**Assessment:** Single-region only. Multi-region would require significant architecture changes (provider region metadata, routing policy extensions, database sharding or replication strategy).

---

## Operational Maturity Assessment

| Area | Rating (1-5) | Notes |
|------|--------------|-------|
| Deployment automation | 2 | Docker exists, no CI/CD |
| Monitoring | 2 | In-memory metrics, optional OTEL |
| Alerting | 1 | None |
| Incident response | 1 | No runbooks, no alerting |
| Configuration management | 3 | Pydantic settings, env vars, YAML configs |
| Backup/recovery | 1 | No backup strategy for SQLite dev DB |
| Capacity planning | 1 | No load testing, no capacity models |
| Change management | 1 | No CI/CD, no quality gates |
| Documentation | 3 | CLAUDE.md, PRD.md, SUMMARY.md exist and are useful |

**Overall Operational Maturity: 1.7 / 5 (Reactive)**

The system can be operated by a developer who knows the codebase. It is not ready for an operations team to manage.
