# backend/app/evaluation/ragas_pipeline.py
# DVMELTSS-FIX: V - Validate, E - Error handling, A - Async, M - Modular
# BATMAN-FIX: A - True async orchestration, T - Concurrent execution
# ASCALE-FIX: L - Layered architecture, E - Error propagation

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union

# DVMELTSS-M: Import centralized utilities
from app.config import get_settings
from app.core.eval_utils import generate_eval_correlation_id
from .ragas_dataset import DatasetManager
from .ragas_evaluator import RAGAsEvaluator, RAGAsSample, RAGAsReport
from .rag_metrics import RAGMetricsCalculator
from .alert_engine import AlertEngine
from .retrieval_metrics import RetrievalEvaluator, RetrievalEvalSuite

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineConfig:
    """Immutable pipeline configuration."""

    domain: str
    dataset_version: str = "latest"
    concurrency: int = 3
    k_retrieval: int = 5
    timeout_seconds: float = 60.0
    enable_alerts: bool = True
    correlation_id: Optional[str] = None  # FIXED: Added to config
    mlflow_run_id: Optional[str] = None

    # Alert thresholds (overrides defaults in AlertEngine)
    alert_thresholds: Dict[str, Dict[str, float]] = field(
        default_factory=lambda: {
            "faithfulness": {"warning": 0.75, "critical": 0.60},
            "answer_relevancy": {"warning": 0.65, "critical": 0.50},
            "context_precision": {"warning": 0.60, "critical": 0.45},
            "context_recall": {"warning": 0.55, "critical": 0.40},
        }
    )

    def __post_init__(self):
        if self.concurrency < 1 or self.concurrency > 10:
            raise ValueError("concurrency must be between 1 and 10")
        if self.k_retrieval < 1 or self.k_retrieval > 20:
            raise ValueError("k_retrieval must be between 1 and 20")


@dataclass
class PipelineResult:
    """Aggregated output from a complete evaluation pipeline run."""

    config: PipelineConfig
    ragas_report: RAGAsReport
    retrieval_report: Optional[RetrievalEvalSuite] = None
    alerts: List[Any] = field(default_factory=list)
    total_samples: int = 0
    successful_evals: int = 0
    failed_evals: int = 0
    total_latency_seconds: float = 0.0
    correlation_id: str = ""  # FIXED: Added for tracing

    @property
    def success_rate(self) -> float:
        return round(self.successful_evals / max(self.total_samples, 1), 3)

    def summary(self) -> dict[str, Any]:
        return {
            "domain": self.config.domain,
            "dataset_version": self.config.dataset_version,
            "total_samples": self.total_samples,
            "success_rate": self.success_rate,
            "failed_count": self.failed_evals,
            "total_latency_seconds": round(self.total_latency_seconds, 2),
            "ragas_summary": self.ragas_report.summary(),
            "retrieval_summary": self.retrieval_report.summary() if self.retrieval_report else None,
            "alerts_triggered": len(self.alerts),
            "correlation_id": self.correlation_id,  # FIXED: Include in summary
            "mlflow_run_id": self.config.mlflow_run_id,
        }


class RAGAsPipeline:
    """
    End-to-end async evaluation pipeline for RAG systems.

    Orchestrates:
    1. Dataset loading & validation
    2. RAG generation (sync or async rag_fn)
    3. RAGAS metric computation (concurrent, structured)
    4. Optional retrieval benchmarking
    5. Threshold-based alert dispatch
    6. Structured report generation for MLflow/LangSmith
    """

    def __init__(
        self,
        config: PipelineConfig,
        openai_api_key: Optional[str] = None,
        correlation_id: Optional[str] = None,  # FIXED: Added param
    ):
        self.config = config
        self.correlation_id = correlation_id or config.correlation_id or generate_eval_correlation_id("pipeline")

        settings = get_settings()
        api_key = openai_api_key or settings.openai_api_key

        if not api_key:
            raise ValueError("OpenAI API key required for evaluation pipeline")

        self.dataset_mgr = DatasetManager()
        self.evaluator = RAGAsEvaluator(model="gpt-4o", eval_model="gpt-4o")
        self.metrics_calc = RAGMetricsCalculator(openai_api_key=api_key)
        self.retrieval_eval = RetrievalEvaluator()
        self.alert_engine = AlertEngine()

        logger.info(
            f"[{self.correlation_id}] RAGAsPipeline initialized: "
            f"domain={config.domain}, concurrency={config.concurrency}"
        )

    async def run(
        self,
        rag_fn: Callable[[str], Union[tuple[str, list[str]], Any]],
        retrieve_fn: Optional[Callable[[str, int], Any]] = None,
        correlation_id: Optional[str] = None,  # FIXED: Added param
    ) -> PipelineResult:
        """Execute full evaluation pipeline."""
        corr_id = correlation_id or self.correlation_id
        start = time.perf_counter()
        total_samples, success, failed = 0, 0, 0

        try:
            # 1️⃣ Load & validate dataset
            dataset = self.dataset_mgr.load(
                domain=self.config.domain,
                version=self.config.dataset_version,
                correlation_id=corr_id,  # FIXED: Propagate
            )
            if not dataset or not dataset.samples:
                raise ValueError(f"No valid samples found for {self.config.domain} v{self.config.dataset_version}")

            total_samples = len(dataset.samples)
            logger.info(f"[{corr_id}] Loaded dataset: {total_samples} samples")

            # 2️⃣ Generate answers & contexts
            samples = await self._run_generation(
                dataset.samples,
                rag_fn,
                correlation_id=corr_id,  # FIXED: Propagate
            )
            success = sum(1 for s in samples if not s.error)
            failed = total_samples - success

            if success == 0:
                logger.error(f"[{corr_id}] All generation calls failed. Aborting evaluation.")
                return PipelineResult(
                    config=self.config,
                    ragas_report=RAGAsReport(samples=[], domain=self.config.domain, correlation_id=corr_id),
                    total_samples=total_samples,
                    successful_evals=0,
                    failed_evals=failed,
                    correlation_id=corr_id,
                )

            # 3️⃣ Run RAGAS evaluation
            logger.info(f"[{corr_id}] Running RAGAS evaluation on {success} successful samples...")
            ragas_report = await self.evaluator.evaluate_dataset(
                samples=samples,
                dataset_name=f"{self.config.domain}_{self.config.dataset_version}",
                domain=self.config.domain,
                concurrency=self.config.concurrency,
                correlation_id=corr_id,  # FIXED: Propagate
            )

            # 4️⃣ Optional retrieval benchmarking
            retrieval_report = None
            # FIXED: hasattr always True (field defined with default ""); check actual non-empty value
            if retrieve_fn and samples and any(s.ground_truth for s in samples):
                logger.info(f"[{corr_id}] Running retrieval evaluation...")
                gt_data = [
                    {"query": s.question, "relevant_chunk_ids": s.ground_truth.split()}
                    for s in samples
                    if s.ground_truth
                ]
                if gt_data:
                    retrieval_report = await self.retrieval_eval.evaluate(
                        ground_truth=gt_data,
                        retrieve_fn=retrieve_fn,
                        k=self.config.k_retrieval,
                        timeout_seconds=self.config.timeout_seconds,
                        concurrency=self.config.concurrency,
                        correlation_id=corr_id,  # FIXED: Propagate
                    )

            # 5️⃣ Check & dispatch alerts
            alerts = []
            if self.config.enable_alerts:
                alerts = self.alert_engine.check_and_send(
                    metrics=ragas_report.summary(),
                    domain=self.config.domain,
                    run_id=self.config.mlflow_run_id or "",
                    correlation_id=corr_id,  # FIXED: Propagate
                )

            total_latency = time.perf_counter() - start
            return PipelineResult(
                config=self.config,
                ragas_report=ragas_report,
                retrieval_report=retrieval_report,
                alerts=alerts,
                total_samples=total_samples,
                successful_evals=success,
                failed_evals=failed,
                total_latency_seconds=total_latency,
                correlation_id=corr_id,  # FIXED: Propagate to result
            )

        except Exception as e:
            logger.error(f"[{corr_id}] Pipeline execution failed: {e}", exc_info=True)
            return PipelineResult(
                config=self.config,
                ragas_report=RAGAsReport(samples=[], domain=self.config.domain, correlation_id=corr_id),
                total_samples=total_samples,
                failed_evals=failed or total_samples,
                correlation_id=corr_id,
            )

    async def _run_generation(
        self,
        raw_samples: list,
        rag_fn: Callable,
        correlation_id: str,
    ) -> list[RAGAsSample]:
        """Run rag_fn concurrently with timeout & error isolation."""
        semaphore = asyncio.Semaphore(self.config.concurrency)
        results: list[RAGAsSample] = []

        async def process_sample(item: dict) -> RAGAsSample:
            async with semaphore:
                question = item.get("question", "")
                ground_truth = item.get("ground_truth", "")
                try:
                    # Handle sync vs async rag_fn
                    if inspect.iscoroutinefunction(rag_fn):
                        answer, contexts = await asyncio.wait_for(rag_fn(question), timeout=self.config.timeout_seconds)
                    else:
                        answer, contexts = await asyncio.wait_for(
                            asyncio.to_thread(rag_fn, question),
                            timeout=self.config.timeout_seconds,
                        )
                    return RAGAsSample(
                        question=question,
                        answer=str(answer or ""),
                        contexts=[str(c) for c in (contexts or [])],
                        ground_truth=str(ground_truth or ""),
                        correlation_id=correlation_id,  # FIXED: Propagate
                    )
                except asyncio.TimeoutError:
                    logger.warning(f"[{correlation_id}] Generation timeout for: {question[:50]}...")
                    return RAGAsSample(
                        question=question,
                        answer="",
                        contexts=[],
                        ground_truth=ground_truth,
                        error="timeout",
                        correlation_id=correlation_id,
                    )
                except Exception as e:
                    logger.warning(
                        f"[{correlation_id}] Generation failed for: {question[:50]}... -> {type(e).__name__}"
                    )
                    return RAGAsSample(
                        question=question,
                        answer="",
                        contexts=[],
                        ground_truth=ground_truth,
                        error=str(e),
                        correlation_id=correlation_id,
                    )

        tasks = [process_sample(s) for s in raw_samples]
        for coro in asyncio.as_completed(tasks):
            results.append(await coro)

        return results


# DVMELTSS-M: Explicit module exports
__all__ = ["RAGAsPipeline", "PipelineConfig", "PipelineResult"]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
