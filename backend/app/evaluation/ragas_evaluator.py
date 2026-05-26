# backend/app/evaluation/ragas_evaluator.py
# DVMELTSS-FIX: V - Validate, E - Error handling, A - Async, M - Modular
# BATMAN-FIX: A - True async, T - Concurrent execution
# ASCALE-FIX: L - Layered, E - Error propagation
# ✅ FIXED: Proper async embedding handling + input validation + per-metric error logging

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Final, List, Optional, Any

import numpy as np

# DVMELTSS-M: Import centralized utilities
from app.config import get_settings
from app.core.llm_pool import get_llm
from app.core.eval_utils import call_llm_with_retry, generate_eval_correlation_id
from app.core.pii_utils import scrub_pii_for_evaluation

logger = logging.getLogger(__name__)

# ✅ NEW: Per-metric timeout (seconds)
_METRIC_TIMEOUT: Final = 60.0


@dataclass
class RAGAsSample:
    """
    A single evaluation sample with all inputs and scores.

    Matches the RAGAs paper's definition:
    - question:      user query
    - answer:        generated answer
    - contexts:      retrieved chunks used for generation
    - ground_truth:  reference answer (for recall computation)
    """
    question: str
    answer: str
    contexts: list[str]  # retrieved chunk texts
    ground_truth: str = ""

    # Computed scores (filled by RAGAsEvaluator)
    faithfulness: float = 0.0
    answer_relevancy: float = 0.0
    context_precision: float = 0.0
    context_recall: float = 0.0

    # Intermediate results for debugging
    faithfulness_claims: list[dict] = field(default_factory=list)
    relevancy_questions: list[str] = field(default_factory=list)
    precision_verdicts: list[dict] = field(default_factory=list)
    recall_attributions: list[dict] = field(default_factory=list)

    # Meta
    eval_model: str = ""
    latency_seconds: float = 0.0
    error: Optional[str] = None
    correlation_id: str = ""

    @property
    def composite_score(self) -> float:
        """Weighted composite score."""
        weights = {
            "faithfulness": 0.35,
            "answer_relevancy": 0.25,
            "context_precision": 0.25,
            "context_recall": 0.15,
        }
        return (
            self.faithfulness * weights["faithfulness"] +
            self.answer_relevancy * weights["answer_relevancy"] +
            self.context_precision * weights["context_precision"] +
            self.context_recall * weights["context_recall"]
        )

    def to_dict(self) -> dict:
        return {
            "question": self.question[:100],
            "faithfulness": round(self.faithfulness, 4),
            "answer_relevancy": round(self.answer_relevancy, 4),
            "context_precision": round(self.context_precision, 4),
            "context_recall": round(self.context_recall, 4),
            "composite_score": round(self.composite_score, 4),
            "latency_seconds": round(self.latency_seconds, 3),
            "error": self.error,
            "correlation_id": self.correlation_id,
        }


@dataclass
class RAGAsReport:
    """Aggregate report across all evaluated samples."""
    samples: list[RAGAsSample]
    dataset_name: str = ""
    eval_model: str = ""
    domain: str = "general"
    correlation_id: str = ""

    @property
    def mean_faithfulness(self) -> float:
        return float(np.mean([s.faithfulness for s in self.samples])) if self.samples else 0.0

    @property
    def mean_answer_relevancy(self) -> float:
        return float(np.mean([s.answer_relevancy for s in self.samples])) if self.samples else 0.0

    @property
    def mean_context_precision(self) -> float:
        return float(np.mean([s.context_precision for s in self.samples])) if self.samples else 0.0

    @property
    def mean_context_recall(self) -> float:
        return float(np.mean([s.context_recall for s in self.samples])) if self.samples else 0.0

    @property
    def mean_composite(self) -> float:
        return float(np.mean([s.composite_score for s in self.samples])) if self.samples else 0.0

    @property
    def failing_samples(self) -> list[RAGAsSample]:
        """Samples with faithfulness < 0.75 — potential hallucinations."""
        return [s for s in self.samples if s.faithfulness < 0.75]

    def summary(self) -> dict:
        return {
            "n_samples": len(self.samples),
            "domain": self.domain,
            "mean_faithfulness": round(self.mean_faithfulness, 4),
            "mean_answer_relevancy": round(self.mean_answer_relevancy, 4),
            "mean_context_precision": round(self.mean_context_precision, 4),
            "mean_context_recall": round(self.mean_context_recall, 4),
            "mean_composite": round(self.mean_composite, 4),
            "failing_count": len(self.failing_samples),
            "failing_ratio": round(len(self.failing_samples) / max(len(self.samples), 1), 4),
            "faithfulness_alert": self.mean_faithfulness < 0.75,
            "correlation_id": self.correlation_id,
        }


class RAGAsEvaluator:
    """
    Implements all four RAGAs metrics:
    1. Faithfulness — are claims supported by context?
    2. Answer Relevancy — does answer address the question?
    3. Context Precision — are retrieved chunks useful?
    4. Context Recall — was all relevant info retrieved?

    Design decisions:
    - All four metrics run concurrently via asyncio.gather()
    - Each metric uses centralized LLM pool with retry logic
    - Embeddings cached via CachedOpenAIEmbeddings
    - Scores always clamped to [0.0, 1.0]
    - Correlation ID propagated for distributed tracing
    """

    def __init__(
        self,
        model: str = "gpt-4o",
        eval_model: str = "gpt-4o",
    ):
        settings = get_settings()
        # FIXED: Use centralized LLM pool instead of creating new client
        self.llm = get_llm(streaming=False, model_override=eval_model, temperature_override=0.0)
        self.model = model
        self.eval_model = eval_model

        from app.vectorstore.embeddings import CachedOpenAIEmbeddings
        self._embedder = CachedOpenAIEmbeddings(
            api_key=settings.openai_api_key,
            cache_dir=".cache/eval_embeddings",
        )

    # ✅ NEW: Sample validation helper
    def _validate_sample(self, sample: RAGAsSample, corr_id: str) -> tuple[bool, str]:
        """Validate sample inputs before evaluation."""
        if not isinstance(sample.question, str) or not sample.question.strip():
            return False, "question must be a non-empty string"
        if not isinstance(sample.answer, str):
            return False, "answer must be a string"
        if not isinstance(sample.contexts, list):
            return False, "contexts must be a list"
        return True, ""

    async def evaluate_sample(
        self, 
        sample: RAGAsSample,
        correlation_id: Optional[str] = None,
    ) -> RAGAsSample:
        """Evaluate all four RAGAs metrics for a single sample."""
        corr_id = correlation_id or sample.correlation_id or generate_eval_correlation_id("ragas")
        sample.correlation_id = corr_id
        
        start = time.perf_counter()

        # ✅ Validate sample
        is_valid, error = self._validate_sample(sample, corr_id)
        if not is_valid:
            logger.error(f"[{corr_id}] Invalid sample: {error}")
            sample.error = error
            sample.latency_seconds = round(time.perf_counter() - start, 3)
            return sample

        try:
            # Run all four metrics concurrently with timeout
            tasks = [
                asyncio.wait_for(self._compute_faithfulness(sample, corr_id), timeout=_METRIC_TIMEOUT),
                asyncio.wait_for(self._compute_answer_relevancy(sample, corr_id), timeout=_METRIC_TIMEOUT),
                asyncio.wait_for(self._compute_context_precision(sample, corr_id), timeout=_METRIC_TIMEOUT),
                asyncio.wait_for(self._compute_context_recall(sample, corr_id), timeout=_METRIC_TIMEOUT),
            ]
            
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # ✅ Handle exceptions with per-metric logging
            def safe_score(result, metric_name: str, default: float = 0.5) -> float:
                if isinstance(result, Exception):
                    logger.warning(f"[{corr_id}] {metric_name} failed: {result}")
                    return default
                return float(np.clip(result, 0.0, 1.0))

            sample.faithfulness = safe_score(results[0], "faithfulness")
            sample.answer_relevancy = safe_score(results[1], "answer_relevancy")
            sample.context_precision = safe_score(results[2], "context_precision")
            sample.context_recall = safe_score(results[3], "context_recall")
            sample.eval_model = self.eval_model
            sample.latency_seconds = round(time.perf_counter() - start, 3)

        except asyncio.TimeoutError:
            logger.error(f"[{corr_id}] Sample evaluation timed out after {_METRIC_TIMEOUT}s")
            sample.error = f"Timeout after {_METRIC_TIMEOUT}s"
        except Exception as e:
            logger.error(f"[{corr_id}] Sample evaluation failed: {e}")
            sample.error = str(e)

        return sample

    async def evaluate_dataset(
        self,
        samples: list[RAGAsSample],
        dataset_name: str = "",
        domain: str = "general",
        concurrency: int = 3,
        correlation_id: Optional[str] = None,
    ) -> RAGAsReport:
        """Evaluate all samples with controlled concurrency."""
        corr_id = correlation_id or generate_eval_correlation_id("ragas_dataset")
        semaphore = asyncio.Semaphore(concurrency)
        evaluated: list[RAGAsSample] = []

        async def evaluate_with_semaphore(s: RAGAsSample) -> RAGAsSample:
            async with semaphore:
                try:
                    return await self.evaluate_sample(s, correlation_id=corr_id)
                except Exception as e:
                    # ✅ FIXED: Handle per-task exceptions without stopping all
                    logger.error(f"[{corr_id}] Failed to evaluate sample: {e}")
                    s.error = str(e)
                    return s

        tasks = [evaluate_with_semaphore(s) for s in samples]

        for i, coro in enumerate(asyncio.as_completed(tasks)):
            try:
                result = await coro
                evaluated.append(result)
                logger.info(
                    f"[{corr_id}] RAGAs [{i+1}/{len(samples)}]: "
                    f"F={result.faithfulness:.3f} "
                    f"AR={result.answer_relevancy:.3f} "
                    f"CP={result.context_precision:.3f} "
                    f"CR={result.context_recall:.3f} "
                    f"composite={result.composite_score:.3f}"
                )
            except Exception as e:
                logger.error(f"[{corr_id}] Task completion failed: {e}")

        return RAGAsReport(
            samples=evaluated,
            dataset_name=dataset_name,
            eval_model=self.eval_model,
            domain=domain,
            correlation_id=corr_id,
        )

    # ======================================================================
    # METRIC 1: Faithfulness
    # ======================================================================
    async def _compute_faithfulness(self, sample: RAGAsSample, corr_id: str) -> float:
        """Faithfulness = supported_claims / total_claims"""
        if not sample.answer or not sample.contexts:
            return 0.0

        # FIXED: Use centralized PII scrubbing
        context_text = "\n\n".join(
            scrub_pii_for_evaluation(c[:300], domain="all") for c in sample.contexts[:5]
        )
        scrubbed_answer = scrub_pii_for_evaluation(sample.answer[:800], domain="all")

        prompt = f"""Extract all factual claims from the answer, then verify each
against the provided context.

Answer: {scrubbed_answer}

Context:
{context_text}

Return ONLY valid JSON:
{{
  "claims": [
    {{"claim": "...", "supported": true, "evidence": "quote or null"}}
  ],
  "supported_count": 3,
  "total_count": 4
}}"""

        try:
            # FIXED: Use centralized async LLM call with retry
            data = await call_llm_with_retry(
                prompt=prompt,
                model=self.eval_model,
                max_tokens=1000,
                temperature=0.0,
                response_format={"type": "json_object"},
                extract_key=None,
                default_value={"claims": [], "supported_count": 0, "total_count": 0},
                correlation_id=corr_id,
            )
            
            if not isinstance(data, dict):
                return 0.5
                
            total = int(data.get("total_count", 0))
            supported = int(data.get("supported_count", 0))
            sample.faithfulness_claims = data.get("claims", [])

            # ✅ FIXED: Safe division
            if total == 0:
                return 1.0
            return min(supported / total, 1.0)

        except Exception as e:
            logger.warning(f"[{corr_id}] Faithfulness computation failed: {e}")
            return 0.5

    # ======================================================================
    # METRIC 2: Answer Relevancy
    # ======================================================================
    async def _compute_answer_relevancy(self, sample: RAGAsSample, corr_id: str) -> float:
        """Answer Relevancy = mean cosine similarity between query and generated questions"""
        if not sample.answer or not sample.question:
            return 0.0

        scrubbed_answer = scrub_pii_for_evaluation(sample.answer[:600], domain="all")
        
        prompt = f"""Generate 3 different questions that this answer is responding to.
Answer: {scrubbed_answer}
Return ONLY valid JSON: {{"questions": ["q1", "q2", "q3"]}}"""

        try:
            # FIXED: Use centralized async LLM call
            data = await call_llm_with_retry(
                prompt=prompt,
                model=self.eval_model,
                max_tokens=200,
                temperature=0.3,
                response_format={"type": "json_object"},
                extract_key="questions",
                default_value=[],
                correlation_id=corr_id,
            )
            
            gen_questions = [q for q in (data or []) if q and len(q.strip()) > 5][:3]
            
            if not gen_questions:
                return 0.5

            sample.relevancy_questions = gen_questions

            # Embed original question and generated questions
            all_texts = [sample.question] + gen_questions
            
            # ✅ FIXED: Check if embed_documents is async and handle accordingly
            if inspect.iscoroutinefunction(self._embedder.embed_documents):
                embeddings = await self._embedder.embed_documents(all_texts)
            else:
                # Run sync embed in thread
                import sys
                if sys.version_info >= (3, 9):
                    embeddings = await asyncio.to_thread(
                        lambda: self._embedder.embed_documents(all_texts)
                    )
                else:
                    loop = asyncio.get_running_loop()  # FIXED: get_event_loop() deprecated in Python 3.10+
                    embeddings = await loop.run_in_executor(
                        None,
                        lambda: self._embedder.embed_documents(all_texts)
                    )

            if len(embeddings) < 2:
                return 0.5

            q_vec = np.array(embeddings[0])
            gen_vecs = np.array(embeddings[1:])

            q_norm = np.linalg.norm(q_vec)
            if q_norm < 1e-8:
                return 0.5

            gen_norms = np.linalg.norm(gen_vecs, axis=1)
            gen_norms = np.where(gen_norms < 1e-8, 1e-8, gen_norms)

            similarities = np.dot(gen_vecs, q_vec) / (gen_norms * q_norm)
            return float(np.clip(np.mean(similarities), 0.0, 1.0))

        except Exception as e:
            logger.warning(f"[{corr_id}] Answer relevancy computation failed: {e}")
            return 0.5

    # ======================================================================
    # METRIC 3: Context Precision
    # ======================================================================
    async def _compute_context_precision(self, sample: RAGAsSample, corr_id: str) -> float:
        """Context Precision = useful_chunks / total_chunks"""
        if not sample.contexts or not sample.answer:
            return 0.0

        contexts_text = "\n\n".join(
            f"[Context {i+1}]: {scrub_pii_for_evaluation(ctx[:300], domain='all')}"
            for i, ctx in enumerate(sample.contexts[:6])
        )
        scrubbed_answer = scrub_pii_for_evaluation(sample.answer[:400], domain="all")
        
        prompt = f"""Question: {sample.question}
Answer: {scrubbed_answer}
Contexts:
{contexts_text}

Return ONLY valid JSON:
{{
  "verdicts": [{{"context_index": 1, "useful": true, "reason": "..."}}],
  "useful_count": 2,
  "total_count": 3
}}"""

        try:
            data = await call_llm_with_retry(
                prompt=prompt,
                model=self.eval_model,
                max_tokens=600,
                temperature=0.0,
                response_format={"type": "json_object"},
                extract_key=None,
                default_value={"verdicts": [], "useful_count": 0, "total_count": 0},
                correlation_id=corr_id,
            )
            
            if not isinstance(data, dict):
                return 0.5
                
            total = int(data.get("total_count", len(sample.contexts)))
            useful = int(data.get("useful_count", 0))
            sample.precision_verdicts = data.get("verdicts", [])

            # ✅ FIXED: Safe division
            if total == 0:
                return 1.0
            return min(useful / total, 1.0)

        except Exception as e:
            logger.warning(f"[{corr_id}] Context precision computation failed: {e}")
            return 0.5

    # ======================================================================
    # METRIC 4: Context Recall
    # ======================================================================
    async def _compute_context_recall(self, sample: RAGAsSample, corr_id: str) -> float:
        """Context Recall = attributed_sentences / total_sentences"""
        if not sample.ground_truth or not sample.contexts:
            return 0.5

        context_text = "\n\n".join(
            scrub_pii_for_evaluation(c[:300], domain="all") for c in sample.contexts[:5]
        )

        sentences = [
            s.strip() for s in re.split(r"(?<=[.!?])\s+", sample.ground_truth)
            if s.strip() and len(s.strip()) > 10
        ]

        if not sentences:
            return 0.5

        sentences_text = "\n".join(f"[{i+1}] {scrub_pii_for_evaluation(s, domain='all')}" for i, s in enumerate(sentences))

        prompt = f"""Reference sentences:
{sentences_text}

Retrieved context:
{context_text}

Return ONLY valid JSON:
{{
  "attributions": [{{"sentence_index": 1, "attributed": true, "evidence": "quote or null"}}],
  "attributed_count": 3,
  "total_count": 4
}}"""

        try:
            data = await call_llm_with_retry(
                prompt=prompt,
                model=self.eval_model,
                max_tokens=800,
                temperature=0.0,
                response_format={"type": "json_object"},
                extract_key=None,
                default_value={"attributions": [], "attributed_count": 0, "total_count": 0},
                correlation_id=corr_id,
            )
            
            if not isinstance(data, dict):
                return 0.5
                
            total = int(data.get("total_count", len(sentences)))
            attributed = int(data.get("attributed_count", 0))
            sample.recall_attributions = data.get("attributions", [])

            # ✅ FIXED: Safe division
            if total == 0:
                return 1.0
            return min(attributed / total, 1.0)

        except Exception as e:
            logger.warning(f"[{corr_id}] Context recall computation failed: {e}")
            return 0.5


def get_evaluator_metadata() -> dict[str, Any]:
    """✅ NEW: Return evaluator metadata for monitoring."""
    return {
        "model": get_settings().openai_chat_model,
        "eval_model": "gpt-4o",
        "metrics": ["faithfulness", "answer_relevancy", "context_precision", "context_recall"],
        "timeout_per_metric": _METRIC_TIMEOUT,
        "composite_weights": {
            "faithfulness": 0.35,
            "answer_relevancy": 0.25,
            "context_precision": 0.25,
            "context_recall": 0.15,
        },
    }


# DVMELTSS-M: Explicit module exports
__all__ = [
    "RAGAsEvaluator",
    "RAGAsSample",
    "RAGAsReport",
    "get_evaluator_metadata",
]
# Local smoke test entry point. Run: python -m 
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)

