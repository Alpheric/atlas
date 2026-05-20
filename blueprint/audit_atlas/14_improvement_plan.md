# Atlas Platform — Final Improvement Plan

> STATUS 2026-05-12: ALL PHASES IMPLEMENTED. Phases 0,1,2,3 complete and pushed
> (commits f6f61bb..c82e12c). Most new subsystems ship OFF by default behind
> settings — see "Morning TODO" at the bottom for the flags to flip and the
> manual decisions remaining.


_Last updated: 2026-05-12_

## North Star

Atlas's moat is **routing + distillation** (teacher→student handoff to cut cost).
Nothing else on the market does this. Guiding principle:

> **Buy/integrate the commodity layers (observability, eval UI, tracing).
> Build only what extends the routing + distillation moat.**

Concretely: stop expanding the hand-rolled dashboard/metrics stack; pipe
telemetry into OTel→Langfuse instead, and reinvest that engineering into
routing, distillation, and the enterprise gaps (prompt versioning, cost
attribution, eval gates).

## Context: how Atlas, Langfuse, and OpenTelemetry relate

- **Langfuse** = LLM observability/LLMOps platform (tracing, prompt mgmt,
  evals). Sits *alongside* the app. Ingests OTLP natively.
- **OpenTelemetry** = vendor-neutral instrumentation standard (traces +
  metrics + logs, SDK, OTLP protocol). The *pipe*, not a backend.
- **Atlas** = the AI gateway/serving + distillation layer. Produces telemetry;
  can speak OTel; should export to Langfuse rather than rebuild it.

Atlas already ships OTel code (`src/a1/common/telemetry.py`) that is only ~40%
wired: metrics + FastAPI HTTP spans exist, but the tracer is never used (no
internal pipeline spans), it's disabled by default (`otlp_endpoint=""`), and
there's no logs signal or trace correlation.

---

## Phase 0 — Close open loops (this week)

| # | Item | File | Effort | Status |
|---|---|---|---|---|
| 0.1 | Purge 5 dead Gemini models; add live ones (`gemini-2.5-flash-lite`, `gemini-flash-latest`, `gemini-pro-latest`) | `config/providers.yaml`, `config/settings.py` | XS | done 2026-05-12 |
| 0.2 | Fix unknown-model -> VeoProvider crash (guard `hasattr(complete)` in fallback) | `proxy/core_pipeline.py` | S | done 2026-05-12 |
| 0.3 | Investigate `deepseek-r1:8b` empty responses (reasoning-token leak vs cold-load) | `providers/` (ollama) | S | open |
| 0.4 | Re-enable a focused lint-only CI | `.github/workflows/` | S | open |

## Phase 1 — Observability (highest leverage; code 90% exists)

| # | Item | Detail | Effort |
|---|---|---|---|
| 1.1 | Enable OTel -> Langfuse | `A1_LANGFUSE_*` settings + OTLP exporter auth header; point `otlp_endpoint` at Langfuse | S |
| 1.2 | Actually use the tracer | Wrap `CorePipeline.execute()`'s 12 steps in `tracer.start_as_current_span()` | M |
| 1.3 | Span attributes | `conversation_id`, `session_id`, `api_key_hash`, `atlas_model`, `task_type`, `provider`, `cache_hit`, `fast_path` | S |
| 1.4 | Auto-instrument SQLAlchemy + httpx | Free DB + outbound-call spans inside each request trace | S |
| 1.5 | Trace-correlate logs | Inject `trace_id` into `get_logger` output | S |
| 1.6 | Retire duplicate metrics (later) | Collapse 3 parallel systems (in-memory singleton, hand-rolled Prometheus, OTel) once a backend covers dashboard needs. Keep DB tables (they power product features) | M |

## Phase 2 — Enterprise & quality gaps

| # | Item | Why | Effort |
|---|---|---|---|
| 2.1 | Prompt versioning | New `prompt_versions` table; move model suffixes + hardcoded `_CRITIQUE_PROMPT` out of YAML/code | M |
| 2.2 | Cost attribution by workspace/user | `usage_records.api_key_hash` exists but analytics aggregate globally; add per-workspace rollups + budget burn-down | M |
| 2.3 | Eval datasets + experiments | Promote curated `dual_execution_records` into versioned eval sets; add a replay runner (dataset x N models/prompts -> scored) | L |
| 2.4 | LLM-as-judge scoring | `quality_scorer.py` is 5 heuristics; add a judge using the Claude teacher -> `signal_type="llm_judge"` | M |
| 2.5 | Anomaly detection + alerting | On stored time-series (p99 spike, error-rate jump, cost surge, provider flap) -> webhook/Slack | M |

## Phase 3 — Extend the moat

| # | Item | Why | Effort |
|---|---|---|---|
| 3.1 | Distillation quality-regression gates | Block adapter graduation (`task_type_readiness.lifecycle_state`) unless it beats incumbent on the eval set | L |
| 3.2 | Routing replay / shadow eval | Re-run historical traffic through a candidate routing policy offline; compare cost/quality before shipping | L |
| 3.3 | Provider failover hardening | Formalize a health-aware circuit breaker with per-provider budgets | M |

## Sequencing

```
Week 1:  Phase 0 (close loops)  +  Phase 1.1-1.3 (OTel on, real spans)
Week 2:  Phase 1.4-1.5  +  Phase 2.2 (cost attribution)  +  2.1 (prompt versioning)
Week 3+: Phase 2.3-2.5 (evals, judge, alerting)
Later:   Phase 3 (gates, replay, failover) + 1.6 (retire dup metrics)
```

**Highest-leverage single move:** Phase 1.1 + 1.2 — enable OTel and instrument
the pipeline. Lowest effort relative to impact; makes every future change
observable and debuggable.

## Reference: current-state audit (2026-05-12)

- **Tracing:** OTLP configured but disabled; tracer created but never called; no
  distributed correlation; no Langfuse.
- **Metrics:** strong but volatile (in-memory singleton + hand-rolled Prometheus
  `/metrics`); durable copies in `usage_records` / `routing_decisions`.
- **Prompts:** no versioning; static YAML suffixes; hardcoded critique template.
- **Quality:** heuristic scorer (`heuristic_v1`) + self-critique + background
  conversation health monitor; no eval datasets / experiment framework.
- **DB:** rich schema (40+ tables) incl. `conversations`, `messages`,
  `routing_decisions`, `quality_signals`, `usage_records`,
  `dual_execution_records`, `task_type_readiness`, `training_runs`.
- **Dashboard:** 15+ analytics endpoints; missing cost attribution by
  user/workspace and anomaly detection.


---

## Morning TODO (decisions + flags for the human)

All code is committed + pushed to both remotes and verified on the running dev
server. These are the things that need YOUR call:

### Turn on (set in .env, then restart backend)
- [ ] **Langfuse tracing** — bring up `docker compose --profile langfuse up -d`,
      create a project at http://localhost:3000, then set
      `A1_LANGFUSE_ENABLED=true` + `A1_LANGFUSE_HOST` + public/secret keys.
- [ ] **LLM-as-judge** — `A1_QUALITY_LLM_JUDGE_ENABLED=true`
      (consider `A1_QUALITY_LLM_JUDGE_SAMPLE_RATE=0.2` to control cost).
- [ ] **Anomaly alerts webhook** — set `A1_ALERT_WEBHOOK_URL=<slack webhook>`
      (detection is already on; this just adds notifications).
- [ ] **Distillation eval gate** — `A1_DISTILLATION_EVAL_GATE_ENABLED=true`
      AFTER you create an eval dataset per task type (see below). Until a
      dataset exists the gate auto-skips, so enabling it now is harmless.

### Already on, no action needed
- Anomaly detection (in-process), circuit breaker, cost attribution,
  prompt registry, lint CI, deepseek-r1 ctx cap.

### Decisions to make
- [ ] **Create eval datasets**: `POST /admin/eval/datasets/promote-from-distillation`
      with `{"dataset_name":"code-eval","task_type":"code","min_quality":0.7}`
      to seed from your distillation history, per task type you care about.
- [ ] **Try a routing-cost projection**:
      `POST /admin/routing/replay {"candidate":{"*":"qwen2.5-coder:7b"},"days":30}`
      — last run projected $104.59 → $0 if all code traffic went local.
      Decide if a partial shift is worth it.
- [ ] **CI**: a lint-only workflow is in `.github/workflows/lint.yml`. Decide if
      you want to re-add tests later (the old test job needed DB/env setup).
- [ ] **format check**: `ruff format` would touch ~14 files; left out of CI for
      now. Run it when you want a one-time formatting pass.

### Known follow-ups (flagged, not blockers)
- OneDesk tenant keys (AtlasApiKey) attribute to `cost-by-key` (by tenant) but
  not `cost-by-workspace` (they use tenant_id, not a workspace UUID).
- Atlas model system-prompt suffixes still live in providers.yaml; they can move
  onto the new prompt registry when you want them versioned/A-B-able.
- deepseek-r1 ctx capped to 16384 — verify it's fast enough on your GPUs under
  real load (worked at ~17s warm in testing).
