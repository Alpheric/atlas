"""OpenTelemetry instrumentation for traces and metrics.

No-op unless either:
  - settings.otlp_endpoint is set (generic OTLP/gRPC collector), or
  - settings.langfuse_enabled with public/secret keys (OTLP/HTTP → Langfuse).

Provides safe span helpers (`span()`, `set_attrs()`) that degrade to no-ops
when tracing is disabled, so call sites never need to guard.
"""

import base64
from contextlib import contextmanager

from a1.common.logging import get_logger

log = get_logger("telemetry")

# Module-level handles — safe to import even when OTLP is not configured.
# When disabled, tracer is a no-op tracer and counters/histograms silently discard data.
tracer = None
request_counter = None
request_duration = None
token_counter = None
cost_counter = None
error_counter = None

_initialized = False


def setup_telemetry(app, settings) -> None:
    """Initialize OpenTelemetry tracing and metrics. No-op if otlp_endpoint is empty."""
    global \
        tracer, \
        request_counter, \
        request_duration, \
        token_counter, \
        cost_counter, \
        error_counter, \
        _initialized

    langfuse_on = bool(
        settings.langfuse_enabled
        and settings.langfuse_public_key
        and settings.langfuse_secret_key
    )

    if (not settings.otlp_endpoint and not langfuse_on) or _initialized:
        log.info("OpenTelemetry disabled (no otlp_endpoint and Langfuse not configured)")
        return

    try:
        from opentelemetry import metrics as otel_metrics
        from opentelemetry import trace
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create(
            {
                "service.name": "atlas",
                "service.version": "0.1.0",
            }
        )

        tracer_provider = TracerProvider(resource=resource)

        if langfuse_on:
            # Langfuse ingests OTLP/HTTP at {host}/api/public/otel with
            # HTTP Basic auth: base64(public_key:secret_key).
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter as HTTPSpanExporter,
            )

            host = settings.langfuse_host.rstrip("/")
            creds = f"{settings.langfuse_public_key}:{settings.langfuse_secret_key}"
            auth = base64.b64encode(creds.encode()).decode()
            span_exporter = HTTPSpanExporter(
                endpoint=f"{host}/api/public/otel/v1/traces",
                headers={"Authorization": f"Basic {auth}"},
            )
            tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
            trace.set_tracer_provider(tracer_provider)
            tracer = trace.get_tracer("a1.proxy")
            FastAPIInstrumentor.instrument_app(app)
            _instrument_libraries()
            _initialized = True
            log.info(f"OpenTelemetry → Langfuse initialized ({host})")
            return

        # Generic OTLP/gRPC path (Tempo, Jaeger, Datadog collector, etc.)
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        tracer_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otlp_endpoint))
        )
        trace.set_tracer_provider(tracer_provider)
        tracer = trace.get_tracer("a1.proxy")

        # Metrics
        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=settings.otlp_endpoint),
            export_interval_millis=30000,
        )
        meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
        otel_metrics.set_meter_provider(meter_provider)
        meter = otel_metrics.get_meter("a1.proxy")

        request_counter = meter.create_counter(
            "a1.requests.total", description="Total proxy requests"
        )
        request_duration = meter.create_histogram(
            "a1.requests.duration_ms", description="Request latency in ms"
        )
        token_counter = meter.create_counter(
            "a1.tokens.total", description="Total tokens processed"
        )
        cost_counter = meter.create_counter("a1.cost.usd", description="Total cost in USD")
        error_counter = meter.create_counter("a1.errors.total", description="Total errors")

        # Auto-instrument FastAPI
        FastAPIInstrumentor.instrument_app(app)
        _instrument_libraries()

        _initialized = True
        log.info(f"OpenTelemetry initialized, exporting to {settings.otlp_endpoint}")

    except ImportError as e:
        log.warning(f"OpenTelemetry packages not available: {e}")
    except Exception as e:
        log.error(f"Failed to initialize OpenTelemetry: {e}")


def _instrument_libraries() -> None:
    """Best-effort auto-instrumentation of SQLAlchemy + httpx for free DB and
    outbound-call spans nested inside each request trace. Silently skips any
    library whose instrumentation package isn't installed."""
    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        SQLAlchemyInstrumentor().instrument()
    except Exception as e:
        log.debug(f"SQLAlchemy instrumentation skipped: {e}")
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
    except Exception as e:
        log.debug(f"httpx instrumentation skipped: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Safe span helpers — no-ops when tracing is disabled, so call sites are clean.
# ─────────────────────────────────────────────────────────────────────────────


@contextmanager
def span(name: str, **attrs):
    """Start a child span. Yields the span (or None when tracing is disabled).

    Usage:
        with span("pipeline.route", atlas_model=m, task_type=t) as sp:
            ...
            set_attrs(sp, provider=p, is_local=False)
    """
    if tracer is None:
        yield None
        return
    clean = {k: v for k, v in attrs.items() if v is not None}
    with tracer.start_as_current_span(name, attributes=clean) as sp:
        yield sp


def set_attrs(sp, **attrs) -> None:
    """Set attributes on a span; no-op if span is None or tracing disabled."""
    if sp is None or not hasattr(sp, "set_attribute"):
        return
    for k, v in attrs.items():
        if v is None:
            continue
        try:
            sp.set_attribute(k, v)
        except Exception:
            pass


def record_otel_request(
    provider: str,
    model: str,
    task_type: str | None,
    latency_ms: int,
    cost_usd: float,
    prompt_tokens: int,
    completion_tokens: int,
    error: bool = False,
):
    """Record OTLP metrics for a request. No-op if not initialized."""
    if request_counter is not None:
        attrs = {"provider": provider, "model": model, "task_type": task_type or "unknown"}
        request_counter.add(1, attrs)
        request_duration.record(latency_ms, {"provider": provider})
        token_counter.add(prompt_tokens, {"type": "prompt", "provider": provider})
        token_counter.add(completion_tokens, {"type": "completion", "provider": provider})
        cost_counter.add(cost_usd, {"provider": provider})
        if error and error_counter:
            error_counter.add(1, attrs)
