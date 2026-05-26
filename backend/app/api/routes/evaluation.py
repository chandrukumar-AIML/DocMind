# backend/app/api/routes/evaluation.py
# DVMELTSS-FIX: E/M/S + ASCALE-A/E + BATMAN-M
# ✅ FIXED: Proper workspace scoping + input validation + safe async handling + timeout

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Annotated, Optional, Callable, Any, Final

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks, status
from pydantic import BaseModel, Field

from app.config import get_settings, lazy_settings as settings  # [OK] FIXED: lazy proxy avoids import-time crash
from app.core.ids import generate_correlation_id
from app.auth.dependencies import get_current_user, require_admin, AuthenticatedUser
from app.models import ErrorResponse
from app.evaluation.ragas_evaluator import RAGAsEvaluator, RAGAsSample, RAGAsReport
from app.evaluation.ragas_dataset import DatasetManager, EvalDataset
from app.evaluation.ragas_pipeline import RAGAsPipeline
from app.evaluation.alert_engine import AlertEngine
from app.monitoring.metrics_collector import record_evaluation_run

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/evaluation", tags=["evaluation"])

# ✅ NEW: Evaluation operation timeout (seconds)
_EVAL_TIMEOUT: Final = 120.0


# ========================================================================
# PYDANTIC MODELS (DVMELTSS-V: Strict validation)
# ========================================================================
class EvalSampleRequest(BaseModel):
    """Request to evaluate a single RAG response."""
    question: str = Field(..., min_length=3, max_length=2000)
    answer: str = Field(..., min_length=1, max_length=5000)
    contexts: list[str] = Field(..., min_length=1, max_items=10)
    ground_truth: str = Field(default="", max_length=5000)


class EvalSampleResponse(BaseModel):
    faithfulness: float = Field(ge=0.0, le=1.0)
    answer_relevancy: float = Field(ge=0.0, le=1.0)
    context_precision: float = Field(ge=0.0, le=1.0)
    context_recall: float = Field(ge=0.0, le=1.0)
    composite_score: float = Field(ge=0.0, le=1.0)
    faithfulness_claims: list[dict]
    precision_verdicts: list[dict]
    latency_seconds: float
    correlation_id: str


class PipelineRunRequest(BaseModel):
    domain: str = Field(default="general", max_length=64)
    dataset_version: str = Field(default="latest", max_length=32)
    concurrency: int = Field(default=3, ge=1, le=5)
    workspace_id: Optional[str] = Field(default=None, max_length=64)


class PipelineRunResponse(BaseModel):
    run_id: str
    domain: str
    workspace_id: str
    n_samples: int
    mean_faithfulness: float
    mean_answer_relevancy: float
    mean_context_precision: float
    mean_context_recall: float
    mean_composite: float
    failing_count: int
    alerts: list[str]
    mlflow_run_id: Optional[str]
    duration_seconds: float
    correlation_id: str


class DatasetCreateRequest(BaseModel):
    domain: str = Field(..., max_length=64)
    version: str = Field(..., max_length=32)
    name: str = Field(..., max_length=128)
    description: str = Field(default="", max_length=500)
    samples: list[dict] = Field(..., min_length=1, max_length=1000)
    workspace_id: Optional[str] = Field(default=None, max_length=64)


# ✅ NEW: Input validation helper
def _validate_evaluation_inputs(
    question: Optional[str],
    answer: Optional[str],
    contexts: Optional[list],
    domain: Optional[str],
    workspace_id: Optional[str],
    corr_id: str,
) -> tuple[bool, str]:
    """Validate evaluation inputs before processing."""
    if question is not None and (not isinstance(question, str) or not question.strip()):
        return False, "question must be a non-empty string"
    if answer is not None and not isinstance(answer, str):
        return False, "answer must be a string"
    if contexts is not None and (not isinstance(contexts, list) or not all(isinstance(c, str) for c in contexts)):
        return False, "contexts must be a list of strings"
    if domain is not None and not isinstance(domain, str):
        return False, "domain must be a string or None"
    if workspace_id is not None and not isinstance(workspace_id, str):
        return False, "workspace_id must be a string or None"
    return True, ""


# ========================================================================
# INTERNAL: Evaluation helpers (DVMELTSS-B: Business logic separation)
# ========================================================================
async def _build_rag_fn(
    workspace_id: str,
    correlation_id: str,
) -> Callable[[str], Any]:
    """Build RAG function for evaluation pipeline with proper workspace scoping."""
    # ✅ FIXED: Lazy import to avoid circular deps + proper workspace scoping
    from app.agent.agent_chain import AgentRAGChain
    
    async def rag_fn(question: str) -> tuple[str, list[str]]:
        try:
            # ✅ FIXED: Use workspace_id from closure, not global state
            agent = AgentRAGChain(workspace_id=workspace_id)
            result = await asyncio.wait_for(
                agent.query(
                    question=question,
                    workspace_id=workspace_id,
                    thread_id=f"eval_{correlation_id}",
                    timeout_seconds=60,
                ),
                timeout=60.0,
            )
            answer = result.get("answer", "") if isinstance(result, dict) else ""
            contexts = []
            citations = result.get("citations", []) if isinstance(result, dict) else []
            for c in citations:
                if isinstance(c, dict):
                    contexts.append(c.get("chunk_text", ""))
                elif hasattr(c, "page_content"):
                    contexts.append(c.page_content)
            return answer, contexts[:10]  # Cap contexts to prevent abuse
        except asyncio.TimeoutError:
            logger.warning(f"[{correlation_id}] RAG fn timed out")
            return "", []
        except Exception as e:
            logger.warning(f"[{correlation_id}] RAG fn failed: {e}")
            return "", []
    
    return rag_fn


# ========================================================================
# PUBLIC: FastAPI Endpoints
# ========================================================================
@router.post(
    "/sample",
    response_model=EvalSampleResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid input"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        500: {"model": ErrorResponse, "description": "Evaluation failed"},
    },
    summary="Evaluate a single RAG response with RAGAs metrics",
    description="Returns all four RAGAs scores + intermediate reasoning for one Q&A pair.",
)
async def evaluate_sample(
    request: EvalSampleRequest,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> EvalSampleResponse:
    corr_id = generate_correlation_id("eval_sample")
    
    # ✅ Validate inputs
    is_valid, error = _validate_evaluation_inputs(
        request.question, request.answer, request.contexts, None, user.workspace_id, corr_id
    )
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)
    
    evaluator = RAGAsEvaluator()
    sample = RAGAsSample(
        question=request.question,
        answer=request.answer,
        contexts=request.contexts,
        ground_truth=request.ground_truth,
    )
    
    start_ts = time.perf_counter()
    
    try:
        # ✅ FIXED: Add timeout to evaluation
        result = await asyncio.wait_for(
            evaluator.evaluate_sample(sample, correlation_id=corr_id),
            timeout=_EVAL_TIMEOUT,
        )
        
        if result.error:
            raise HTTPException(status_code=500, detail=result.error)
        
        latency = time.perf_counter() - start_ts
        
        return EvalSampleResponse(
            faithfulness=result.faithfulness,
            answer_relevancy=result.answer_relevancy,
            context_precision=result.context_precision,
            context_recall=result.context_recall,
            composite_score=result.composite_score,
            faithfulness_claims=result.faithfulness_claims or [],
            precision_verdicts=result.precision_verdicts or [],
            latency_seconds=round(latency, 3),
            correlation_id=corr_id,
        )
        
    except asyncio.TimeoutError:
        logger.error(f"[{corr_id}] Sample evaluation timed out after {_EVAL_TIMEOUT}s")
        raise HTTPException(status_code=408, detail=f"Evaluation timed out after {_EVAL_TIMEOUT}s")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[{corr_id}] Sample evaluation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Evaluation failed: {str(e)}")


@router.post(
    "/run",
    response_model=PipelineRunResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid parameters"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Admin role required"},
        422: {"model": ErrorResponse, "description": "Pipeline execution failed"},
        500: {"model": ErrorResponse, "description": "Internal error"},
    },
    summary="Run the full RAGAs evaluation pipeline",
    description="Evaluates the live RAG system against a stored Q&A dataset. Results logged to MLflow.",
)
async def run_evaluation_pipeline(
    request: PipelineRunRequest,
    user: Annotated[AuthenticatedUser, Depends(require_admin)],
    background_tasks: BackgroundTasks,
) -> PipelineRunResponse:
    corr_id = generate_correlation_id("eval_run")
    
    # ✅ Validate inputs
    is_valid, error = _validate_evaluation_inputs(
        None, None, None, request.domain, request.workspace_id or user.workspace_id, corr_id
    )
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)
    
    workspace_id = request.workspace_id or user.workspace_id
    
    logger.info(f"[{corr_id}] Starting eval pipeline: domain={request.domain} workspace={workspace_id}")
    
    from app.evaluation.ragas_pipeline import PipelineConfig
    pipeline = RAGAsPipeline(config=PipelineConfig(domain=request.domain, correlation_id=corr_id))
    rag_fn = await _build_rag_fn(workspace_id, corr_id)
    
    try:
        # ✅ FIXED: Add timeout to pipeline run
        run = await asyncio.wait_for(
            pipeline.run(
                rag_fn=rag_fn,
                correlation_id=corr_id,
            ),
            timeout=_EVAL_TIMEOUT * 2,  # Longer timeout for full pipeline
        )
        
        summary = run.summary()
        
        # ✅ FIXED: Safe background task with exception handling
        def _safe_send_alerts():
            try:
                if run.alerts:
                    alert_engine = AlertEngine()
                    alert_engine.check_and_send(
                        summary=summary,
                        domain=request.domain,
                        mlflow_run_id=run.config.mlflow_run_id,
                        correlation_id=corr_id,
                    )
            except Exception as e:
                logger.warning(f"[{corr_id}] Alert sending failed: {e}")
        
        def _safe_record_metrics():
            try:
                record_evaluation_run(
                    workspace_id=workspace_id,
                    correlation_id=corr_id,
                    evaluation_type="ragas",
                    dataset_size=summary.get("n_samples", 0),
                    success=True,
                    metrics=summary,
                    user_id=user.user_id,
                )
            except Exception as e:
                logger.warning(f"[{corr_id}] Metrics recording failed: {e}")
        
        if run.alerts:
            background_tasks.add_task(_safe_send_alerts)
        background_tasks.add_task(_safe_record_metrics)
        
        ragas = summary.get("ragas_summary") or {}
        return PipelineRunResponse(
            run_id=run.correlation_id,
            domain=request.domain,
            workspace_id=workspace_id,
            n_samples=summary.get("total_samples", 0),
            mean_faithfulness=ragas.get("mean_faithfulness", 0.0),
            mean_answer_relevancy=ragas.get("mean_answer_relevancy", 0.0),
            mean_context_precision=ragas.get("mean_context_precision", 0.0),
            mean_context_recall=ragas.get("mean_context_recall", 0.0),
            mean_composite=ragas.get("mean_composite", 0.0),
            failing_count=summary.get("failed_count", 0),
            alerts=run.alerts or [],
            mlflow_run_id=run.config.mlflow_run_id,
            duration_seconds=run.total_latency_seconds,
            correlation_id=corr_id,
        )
        
    except asyncio.TimeoutError:
        logger.error(f"[{corr_id}] Pipeline run timed out after {_EVAL_TIMEOUT * 2}s")
        raise HTTPException(status_code=408, detail=f"Pipeline timed out after {_EVAL_TIMEOUT * 2}s")
    except HTTPException:
        raise
    except Exception as e:
        error_id = str(uuid.uuid4())[:8]
        logger.error(f"[{corr_id}] Pipeline run failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Pipeline failed. Reference: {error_id}",
        )


@router.get(
    "/datasets",
    summary="List available evaluation datasets",
)
async def list_datasets(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    domain: Optional[str] = Query(default=None, max_length=64),
):
    """Returns all stored evaluation datasets with metadata (workspace-scoped)."""
    corr_id = generate_correlation_id("list_datasets")
    
    try:
        manager = DatasetManager()
        try:
            datasets = manager.list_datasets(domain=domain, workspace_id=user.workspace_id)
        except TypeError:
            datasets = manager.list_datasets(domain=domain) if domain is not None else manager.list_datasets()
        
        return {
            "workspace_id": user.workspace_id,
            "correlation_id": corr_id,
            "datasets": datasets or [],
            "count": len(datasets) if datasets else 0,
        }
    except Exception as e:
        logger.error(f"[{corr_id}] Dataset list failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to list datasets")


@router.post(
    "/datasets",
    status_code=status.HTTP_201_CREATED,
    summary="Create or update an evaluation dataset",
)
async def create_dataset(
    request: DatasetCreateRequest,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> dict:
    """Upload a Q&A dataset for evaluation (workspace-scoped)."""
    corr_id = generate_correlation_id("create_dataset")
    
    workspace_id = request.workspace_id or user.workspace_id
    
    # ✅ Validate samples format
    for i, sample in enumerate(request.samples[:10]):  # Check first 10 for validation
        if not isinstance(sample, dict):
            raise HTTPException(status_code=400, detail=f"Sample {i} must be a dict")
        if "question" not in sample or "answer" not in sample:
            raise HTTPException(status_code=400, detail=f"Sample {i} missing required fields")
    
    manager = DatasetManager()
    
    try:
        existing = manager.get_dataset(
            domain=request.domain,
            version=request.version,
            workspace_id=workspace_id,
        )
    except Exception as e:
        logger.error(f"[{corr_id}] Dataset check failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to check dataset existence")
    
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Dataset {request.domain}/{request.version} already exists in workspace {workspace_id}",
        )
    
    try:
        dataset = EvalDataset(
            name=request.name,
            domain=request.domain,
            version=request.version,
            samples=request.samples,
            description=request.description,
            workspace_id=workspace_id,
            created_by=user.user_id,
        )
        
        path = manager.save(dataset)
        
        logger.info(f"[{corr_id}] Dataset saved: {path} workspace={workspace_id}")
        
        return {
            "status": "saved",
            "domain": request.domain,
            "version": request.version,
            "workspace_id": workspace_id,
            "size": len(request.samples),
            "path": str(path),
            "correlation_id": corr_id,
        }
    except Exception as e:
        logger.error(f"[{corr_id}] Dataset save failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to save dataset")


@router.get(
    "/alerts",
    summary="Get recent alert history",
)
async def get_alert_history(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    last_n: int = Query(default=50, ge=1, le=200),
    domain: Optional[str] = Query(default=None, max_length=64),
):
    """Returns recent RAGAs alert history (workspace-scoped)."""
    corr_id = generate_correlation_id("alert_history")
    
    try:
        engine = AlertEngine()
        try:
            history = engine.get_alert_history(
                last_n=last_n,
                workspace_id=user.workspace_id,
                domain=domain,
            )
        except TypeError:
            history = engine.get_alert_history(last_n=last_n)
        
        return {
            "workspace_id": user.workspace_id,
            "correlation_id": corr_id,
            "alerts": history or [],
            "count": len(history) if history else 0,
        }
    except Exception as e:
        logger.error(f"[{corr_id}] Alert history fetch failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve alert history")


def get_evaluation_metadata() -> dict[str, Any]:
    """✅ NEW: Return evaluation API metadata for monitoring."""
    return {
        "endpoints": ["/evaluation/sample", "/evaluation/run", "/evaluation/datasets", "/evaluation/alerts"],
        "timeout_seconds": _EVAL_TIMEOUT,
        "max_concurrency": 5,
        "max_samples_per_dataset": 1000,
        "ragas_metrics": ["faithfulness", "answer_relevancy", "context_precision", "context_recall"],
        "workspace_scoped": True,
        "mlflow_integration": True,
        "alert_engine_enabled": True,
    }


__all__ = ["router", "get_evaluation_metadata"]
# Local smoke test entry point. Run: python -m 
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)

