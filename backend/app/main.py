# backend/app/main.py
# DVMELTSS-FIX: M - Modular, E - Error handling, S - Security
# ASCALE-FIX: L - Layered, S - Separation, E - Error propagation
# OWASP-FIX: 1 - Prompt safety, 3 - Credential safety
from __future__ import annotations

import asyncio
import logging
import sys
import os
import time
from contextlib import asynccontextmanager
from typing import Awaitable, Callable, Any, Final

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings, lazy_settings as settings  # [OK] FIXED: lazy proxy avoids import-time crash
from app.core.logging_config import configure_logging
from app.core.exceptions import DocuMindError, ValidationError, NotFoundError
from .security import add_security_headers, add_correlation_id

# Configure logging FIRST
configure_logging(level="DEBUG" if settings.api_reload else "INFO")
logger = logging.getLogger(__name__)

# ✅ Startup operation timeout (seconds)
_STARTUP_TIMEOUT: Final = 120.0


# -- Middleware -----------------------------------------------
class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log requests with correlation_id and duration."""
    
    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        start_time = time.perf_counter()
        corr_id = getattr(request.state, "correlation_id", "unknown")
        
        log_level = logging.DEBUG if settings.api_reload else logging.INFO
        if request.url.path in ["/health", "/ready", "/live"]:
            # FIXED: Keep request logs ASCII-safe for Windows consoles.
            logger.log(log_level, f"[{corr_id}] HEALTH -> {request.method} {request.url.path}")
        else:
            logger.info(f"[{corr_id}] -> {request.method} {request.url.path}")
        
        try:
            response = await call_next(request)
            duration = (time.perf_counter() - start_time) * 1000
            
            if request.url.path in ["/health", "/ready", "/live"]:
                logger.log(log_level, f"[{corr_id}] HEALTH <- {response.status_code} {duration:.0f}ms")
            else:
                logger.info(f"[{corr_id}] <- {request.method} {request.url.path} {response.status_code} {duration:.0f}ms")
            return response
            
        except Exception as e:
            logger.error(f"[{corr_id}] ERROR {request.method} {request.url.path} {e}", exc_info=True)
            raise


# ✅ Startup config validation helper
def _validate_startup_config() -> tuple[bool, list[str]]:
    """Validate startup configuration before initialization."""
    errors = []
    
    if not isinstance(settings.api_host, str) or not settings.api_host:
        errors.append("api_host must be a non-empty string")
    if not isinstance(settings.api_port, int) or settings.api_port < 1 or settings.api_port > 65535:
        errors.append("api_port must be between 1 and 65535")
    if not isinstance(settings.app_name, str) or not settings.app_name:
        errors.append("app_name must be a non-empty string")
    if not isinstance(settings.app_version, str):
        errors.append("app_version must be a string")
    
    return len(errors) == 0, errors


# -- Lifespan: Startup/Shutdown -------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle with async-safe initialization."""
    settings = get_settings()
    logger.info(f"Starting {settings.app_name} v{settings.app_version}")
    
    is_valid, errors = _validate_startup_config()
    if not is_valid:
        logger.error(f"Startup config validation failed: {errors}")
        app.state.startup_errors = errors
    else:
        app.state.startup_errors = []
    
    try:
        loop = asyncio.get_running_loop()

        from app.database.schema import ensure_auth_schema, ensure_provenance_schema
        from app.core.webhook_dispatcher import ensure_webhook_schema
        from app.core.comparison_engine import ensure_comparison_schema
        from app.core.workflow_engine import ensure_workflow_schema
        from app.core.annotation_store import ensure_annotation_schema
        from app.core.template_extractor import ensure_template_schema
        from app.core.esign_handler import ensure_esign_schema
        from app.core.compliance_checker import ensure_compliance_schema
        from app.core.invite_manager import ensure_invite_schema
        from app.core.superadmin_utils import ensure_superadmin_schema
        try:
            await asyncio.wait_for(ensure_auth_schema(), timeout=_STARTUP_TIMEOUT)
            await asyncio.wait_for(ensure_provenance_schema(), timeout=_STARTUP_TIMEOUT)
            await asyncio.wait_for(ensure_webhook_schema(), timeout=_STARTUP_TIMEOUT)
            await asyncio.wait_for(ensure_comparison_schema(), timeout=_STARTUP_TIMEOUT)
            await asyncio.wait_for(ensure_workflow_schema(), timeout=_STARTUP_TIMEOUT)
            await asyncio.wait_for(ensure_annotation_schema(), timeout=_STARTUP_TIMEOUT)
            await asyncio.wait_for(ensure_template_schema(), timeout=_STARTUP_TIMEOUT)
            await asyncio.wait_for(ensure_esign_schema(), timeout=_STARTUP_TIMEOUT)
            await asyncio.wait_for(ensure_compliance_schema(), timeout=_STARTUP_TIMEOUT)
            await asyncio.wait_for(ensure_invite_schema(), timeout=_STARTUP_TIMEOUT)
            await asyncio.wait_for(ensure_superadmin_schema(), timeout=_STARTUP_TIMEOUT)
        except asyncio.TimeoutError:
            logger.error(f"Database schema setup timed out after {_STARTUP_TIMEOUT}s")
            app.state.startup_errors.append("Database schema setup timeout")
        except Exception as e:
            logger.warning(f"Database schema setup failed (non-critical): {e}")

        app.state.ocr_pipeline = None
        app.state.store_manager = None
        app.state.rag_chain = None
        app.state.neo4j_store = None

        if settings.eager_startup_services:
            from app.dependencies import get_ocr_pipeline, get_store_manager, get_rag_chain

            try:
                app.state.ocr_pipeline = await asyncio.wait_for(
                    loop.run_in_executor(None, get_ocr_pipeline),
                    timeout=_STARTUP_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.error(f"OCR pipeline init timed out after {_STARTUP_TIMEOUT}s")
                app.state.startup_errors.append("OCR pipeline init timeout")
            except Exception as e:
                logger.warning(f"OCR pipeline init failed: {e}")

            try:
                app.state.store_manager = await asyncio.wait_for(
                    loop.run_in_executor(None, get_store_manager),
                    timeout=_STARTUP_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.error(f"Store manager init timed out after {_STARTUP_TIMEOUT}s")
                app.state.startup_errors.append("Store manager init timeout")
            except Exception as e:
                logger.warning(f"Store manager init failed: {e}")

            try:
                rag_chain = await asyncio.wait_for(
                    loop.run_in_executor(None, get_rag_chain),
                    timeout=_STARTUP_TIMEOUT,
                )
                if rag_chain and hasattr(rag_chain, "initialize"):
                    await asyncio.wait_for(rag_chain.initialize(), timeout=_STARTUP_TIMEOUT)
                app.state.rag_chain = rag_chain
            except asyncio.TimeoutError:
                logger.error(f"RAG chain init timed out after {_STARTUP_TIMEOUT}s")
                app.state.startup_errors.append("RAG chain init timeout")
            except Exception as e:
                logger.warning(f"RAG chain init failed: {e}")

            try:
                ocr = app.state.ocr_pipeline
                if ocr and hasattr(ocr, "warmup"):
                    logger.info("Warming up OCR pipeline...")
                    await asyncio.wait_for(
                        loop.run_in_executor(None, ocr.warmup),
                        timeout=30.0,
                    )
                    logger.info("OCR pipeline pre-warmed")
            except Exception as e:
                logger.warning(f"OCR warmup skipped (non-critical): {e}")

            try:
                from app.graph.neo4j_store import get_neo4j_store
                app.state.neo4j_store = await asyncio.wait_for(
                    loop.run_in_executor(None, get_neo4j_store),
                    timeout=_STARTUP_TIMEOUT,
                )
                logger.info("Neo4j graph store connected")
            except Exception as e:
                logger.warning(f"Neo4j initialization skipped: {e}")

            try:
                from app.cache import get_cache
                cache = await asyncio.wait_for(get_cache(), timeout=_STARTUP_TIMEOUT)
                if cache:
                    logger.info("Cache initialized")
                else:
                    logger.warning("Cache not initialized (using no-op fallback)")
            except Exception as e:
                logger.warning(f"Cache initialization failed (using fallback): {e}")
        else:
            logger.info("Eager startup services disabled; OCR/vector/RAG/graph/cache will lazy-load when needed")

        try:
            from app.observability.langsmith_config import configure_langsmith
            if configure_langsmith():
                logger.info("LangSmith tracing enabled")
        except Exception as e:
            logger.warning(f"LangSmith configuration failed: {e}")
        
        logger.info("Core services initialized successfully")

        # Pre-warm CrossEncoder and check OpenAI status in background (don't block startup)
        async def _prewarm():
            try:
                await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: __import__("app.rag.reranker", fromlist=["get_reranker"]).get_reranker().model),
                    timeout=60.0,
                )
                logger.info("CrossEncoder pre-warmed ✅")
            except Exception as e:
                logger.warning(f"CrossEncoder pre-warm failed (will load on first query): {e}")
            try:
                from app.vectorstore.embeddings import CachedOpenAIEmbeddings
                from app.config import get_settings as _gs
                _s = _gs()
                if getattr(_s, "openai_api_key", None):
                    _emb = CachedOpenAIEmbeddings(api_key=_s.openai_api_key)
                    await asyncio.wait_for(loop.run_in_executor(None, lambda: _emb.embed_query("startup check")), timeout=10.0)
                    logger.info("OpenAI embeddings available ✅")
            except Exception as e:
                logger.warning(f"OpenAI startup check failed (will use local fallback): {e}")
        asyncio.create_task(_prewarm())

    except Exception as e:
        error_msg = f"Startup failed: {e}"
        logger.error(error_msg, exc_info=True)
        app.state.startup_errors.append(error_msg)

    yield
    
    logger.info("Shutting down...")
    
    try:
        from app.cache import get_cache
        cache = await get_cache()
        if cache and hasattr(cache, "close"):
            await asyncio.wait_for(cache.close(), timeout=30.0)
            logger.debug("QueryCache connection closed")
    except Exception as e:
        logger.warning(f"Cache cleanup failed: {e}")
    
    logger.info("Shutdown complete")


# -- Create FastAPI App ---------------------------------------
def create_app() -> FastAPI:
    """Factory function for creating the FastAPI application."""
    settings = get_settings()
    
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="DocuMind AI: Multi-domain Document Intelligence Platform",
        lifespan=lifespan,
        docs_url="/docs" if settings.api_reload else None,
        redoc_url="/redoc" if settings.api_reload else None,
    )
    
    # -- Middleware Stack ---------------------
    cors_origins = settings.cors_origins if settings.cors_origins else []
    if not cors_origins and settings.api_reload:
        cors_origins = ["http://localhost:3000", "http://127.0.0.1:3000"]
    
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Correlation-ID"],
    )
    
    app.middleware("http")(add_correlation_id)
    app.middleware("http")(add_security_headers)
    app.add_middleware(RequestLoggingMiddleware)
    
    # -- Exception Handlers -------
    
    @app.exception_handler(DocuMindError)
    async def handle_documind_error(request: Request, exc: DocuMindError):
        corr_id = getattr(request.state, "correlation_id", "unknown")
        logger.warning(f"[{corr_id}] DocuMindError: {exc.error_code} - {exc}")
        return JSONResponse(
            status_code=exc.status_code,
            content={**exc.to_api_response(), "correlation_id": corr_id},
            headers={"X-Correlation-ID": corr_id},
        )
    
    @app.exception_handler(ValidationError)
    async def handle_validation_error(request: Request, exc: ValidationError):
        corr_id = getattr(request.state, "correlation_id", "unknown")
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"error": "validation_failed", "detail": str(exc), "correlation_id": corr_id},
            headers={"X-Correlation-ID": corr_id},
        )
    
    @app.exception_handler(NotFoundError)
    async def handle_not_found(request: Request, exc: NotFoundError):
        corr_id = getattr(request.state, "correlation_id", "unknown")
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "not_found", "detail": str(exc), "correlation_id": corr_id},
            headers={"X-Correlation-ID": corr_id},
        )
    
    @app.exception_handler(Exception)
    async def handle_generic_error(request: Request, exc: Exception):
        corr_id = getattr(request.state, "correlation_id", "unknown")
        logger.error(f"[{corr_id}] Unhandled exception: {exc}", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "internal_error",
                "detail": str(exc) if settings.api_reload else "An unexpected error occurred",
                "correlation_id": corr_id,
            },
            headers={"X-Correlation-ID": corr_id},
        )
    
    # -- ROUTES -----------------------------------------------
    from app.api.routes import (
        health, auth, query, ingest, documents, agent, graph, retrieval,
        extraction, evaluation, provenance, workspace, versioning,
        tasks, monitoring, finetuning, domains,
        webhooks, comparison, workflows, templates,
        annotations as annotations_route,
        esignature, compliance, superadmin, onboarding, regional,
        apikeys, audit,
    )

    app.include_router(health.router, prefix="", tags=["health"])

    api_prefix = "/api/v1"
    app.include_router(auth.router, prefix=api_prefix, tags=["auth"])
    app.include_router(query.router, prefix=api_prefix, tags=["query"])
    app.include_router(ingest.router, prefix=api_prefix, tags=["ingest"])
    app.include_router(documents.router, prefix=api_prefix, tags=["documents"])
    app.include_router(agent.router, prefix=api_prefix, tags=["agent"])
    app.include_router(graph.router, prefix=api_prefix, tags=["graph"])
    app.include_router(retrieval.router, prefix=api_prefix, tags=["retrieval"])
    app.include_router(extraction.router, prefix=api_prefix, tags=["extraction"])
    app.include_router(evaluation.router, prefix=api_prefix, tags=["evaluation"])
    app.include_router(provenance.router, prefix=api_prefix, tags=["provenance"])
    app.include_router(workspace.router, prefix=api_prefix, tags=["workspace"])
    app.include_router(versioning.router, prefix=api_prefix, tags=["versioning"])
    app.include_router(tasks.router, prefix=api_prefix, tags=["tasks"])
    app.include_router(monitoring.router, prefix=api_prefix, tags=["monitoring"])
    app.include_router(finetuning.router, prefix=api_prefix, tags=["finetuning"])
    app.include_router(domains.router, prefix=api_prefix, tags=["domains"])
    # ── New feature routers ───────────────────────────────────
    app.include_router(webhooks.router, prefix=api_prefix, tags=["webhooks"])
    app.include_router(comparison.router, prefix=api_prefix, tags=["comparison"])
    app.include_router(workflows.router, prefix=api_prefix, tags=["workflows"])
    app.include_router(annotations_route.router, prefix=api_prefix, tags=["annotations"])
    app.include_router(templates.router, prefix=api_prefix, tags=["templates"])
    app.include_router(esignature.router, prefix=api_prefix, tags=["esignature"])
    app.include_router(compliance.router, prefix=api_prefix, tags=["compliance"])
    app.include_router(superadmin.router, prefix=api_prefix, tags=["superadmin"])
    app.include_router(onboarding.router, prefix=api_prefix, tags=["onboarding"])
    app.include_router(regional.router, prefix=api_prefix, tags=["regional"])
    app.include_router(apikeys.router, prefix=api_prefix, tags=["apikeys"])
    app.include_router(audit.router, prefix=api_prefix, tags=["audit"])

    # Backward-compatible auth aliases used by earlier tests and Swagger clients.
    app.add_api_route(f"{api_prefix}/verify-email", auth.verify_email, methods=["POST"], tags=["auth"])
    app.add_api_route(f"{api_prefix}/token", auth.oauth2_token, methods=["POST"], tags=["auth"], include_in_schema=False)
    
    @app.get("/", tags=["root"])
    async def root():
        return {
            "service": settings.app_name,
            "version": settings.app_version,
            "status": "running",
            "docs": "/docs" if settings.api_reload else None,
        }
    
    return app


# -- Application Instance -------------------------------------
app = create_app()


# -- CLI Entry Point ------------------------------------------
if __name__ == "__main__":
    import uvicorn
    
    settings = get_settings()
    
    is_valid, errors = _validate_startup_config()
    if not is_valid:
        logger.error(f"Cannot start server: {errors}")
        sys.exit(1)
    
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.api_reload,
        reload_dirs=["backend/app"] if settings.api_reload else [],
        timeout_keep_alive=300,
        log_level="debug" if settings.api_reload else "info",
    )


def get_main_metadata() -> dict[str, Any]:
    """Return main app metadata for monitoring."""
    return {
        "app_name": settings.app_name,
        "app_version": settings.app_version,
        "api_prefix": "/api/v1",
        "startup_timeout_seconds": _STARTUP_TIMEOUT,
        "cors_enabled": bool(settings.cors_origins) if settings.api_reload else False,
        "docs_enabled": settings.api_reload,
        "middleware_stack": [
            "CORSMiddleware",
            "add_correlation_id",
            "add_security_headers",
            "RequestLoggingMiddleware",
        ],
        "exception_handlers": [
            "DocuMindError",
            "ValidationError",
            "NotFoundError",
            "Exception",
        ],
    }


__all__ = ["app", "create_app", "lifespan", "get_main_metadata"]

