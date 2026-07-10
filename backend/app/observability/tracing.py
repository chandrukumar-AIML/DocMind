"""
OpenTelemetry distributed tracing setup for DocMind AI.

Instruments FastAPI, SQLAlchemy (asyncpg), Redis, and Celery workers so that
a single user request produces a connected trace across all service hops.

Usage (in lifespan or app factory):
    from app.observability.tracing import configure_tracing
    configure_tracing()

All instrumentation is optional — if opentelemetry packages are not installed
(or OTEL_EXPORTER_OTLP_ENDPOINT is not set) the function returns silently so
the app continues to work without tracing.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def configure_tracing(service_name: str = "documind-backend") -> bool:
    """
    Set up OTel SDK with OTLP gRPC exporter.

    Returns True if tracing was successfully configured, False otherwise.
    Requires:
        pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc
                    opentelemetry-instrumentation-fastapi
                    opentelemetry-instrumentation-sqlalchemy
                    opentelemetry-instrumentation-redis
                    opentelemetry-instrumentation-celery
    """
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        logger.debug("OTEL_EXPORTER_OTLP_ENDPOINT not set — tracing disabled")
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource, SERVICE_NAME
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    except ImportError:
        logger.info("opentelemetry-sdk not installed — tracing disabled. "
                    "Run: pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc")
        return False

    try:
        resource = Resource.create({SERVICE_NAME: service_name})
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=endpoint, insecure=not endpoint.startswith("https"))
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        _instrument_fastapi()
        _instrument_sqlalchemy()
        _instrument_redis()
        _instrument_celery()

        logger.info(f"OpenTelemetry tracing configured (service={service_name}, endpoint={endpoint})")
        return True

    except Exception as exc:
        logger.warning(f"OTel tracing setup failed (non-fatal): {exc}")
        return False


def _instrument_fastapi() -> None:
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor().instrument()
        logger.debug("OTel: FastAPI instrumented")
    except ImportError:
        logger.debug("opentelemetry-instrumentation-fastapi not installed — FastAPI spans disabled")
    except Exception as exc:
        logger.debug(f"OTel: FastAPI instrumentation failed: {exc}")


def _instrument_sqlalchemy() -> None:
    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        from app.database.engine import async_engine
        SQLAlchemyInstrumentor().instrument(engine=async_engine.sync_engine)
        logger.debug("OTel: SQLAlchemy instrumented")
    except ImportError:
        logger.debug("opentelemetry-instrumentation-sqlalchemy not installed — DB spans disabled")
    except Exception as exc:
        logger.debug(f"OTel: SQLAlchemy instrumentation failed: {exc}")


def _instrument_redis() -> None:
    try:
        from opentelemetry.instrumentation.redis import RedisInstrumentor
        RedisInstrumentor().instrument()
        logger.debug("OTel: Redis instrumented")
    except ImportError:
        logger.debug("opentelemetry-instrumentation-redis not installed — Redis spans disabled")
    except Exception as exc:
        logger.debug(f"OTel: Redis instrumentation failed: {exc}")


def _instrument_celery() -> None:
    try:
        from opentelemetry.instrumentation.celery import CeleryInstrumentor
        CeleryInstrumentor().instrument()
        logger.debug("OTel: Celery instrumented")
    except ImportError:
        logger.debug("opentelemetry-instrumentation-celery not installed — Celery spans disabled")
    except Exception as exc:
        logger.debug(f"OTel: Celery instrumentation failed: {exc}")


def get_tracer(name: str = "documind"):
    """Return a named OTel tracer for manual span creation."""
    try:
        from opentelemetry import trace
        return trace.get_tracer(name)
    except ImportError:
        return _NoOpTracer()


class _NoOpTracer:
    """Fallback tracer that does nothing when OTel is not installed."""

    def start_as_current_span(self, name: str, **kwargs):
        from contextlib import contextmanager

        @contextmanager
        def _noop():
            yield None

        return _noop()
