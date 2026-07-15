
from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from typing import Final, List, Optional, Any

import numpy as np

# Lazy imports — these heavy/optional packages must not crash the server at
# startup if they are absent from the container.  They are imported inside
# RAGMetricsCalculator.__init__ so any ImportError surfaces only when the
# evaluation feature is actually used, not on every uvicorn boot.
_sentence_bleu = None
_SmoothingFunction = None
_rouge_scorer = None


def _load_optional_eval_deps():
    global _sentence_bleu, _SmoothingFunction, _rouge_scorer
    if _rouge_scorer is None:
        try:
            from nltk.translate.bleu_score import sentence_bleu as _sb, SmoothingFunction as _sf  # noqa: PLC0415
            _sentence_bleu = _sb
            _SmoothingFunction = _sf
        except ImportError:
            pass
        try:
            from rouge_score import rouge_scorer as _rs  # noqa: PLC0415
            _rouge_scorer = _rs
        except ImportError:
            pass

# DVMELTSS-M: Import centralized utilities
from app.config import get_settings
from app.core.llm_pool import get_llm
from app.core.retry import retry_async, RetryConfig
from app.core.eval_utils import call_llm_with_retry, generate_eval_correlation_id
from app.core.pii_utils import scrub_pii_for_evaluation
from app.vectorstore.embeddings import CachedOpenAIEmbeddings

logger = logging.getLogger(__name__)

COMPOSITE_METRICS: Final = ["faithfulness", "answer_relevance", "context_precision"]

_METRIC_TIMEOUT: Final = 60.0


@dataclass
class RAGASMetrics:
    """Structured metrics for a single RAG evaluation sample."""

    query: str
    answer: str
    contexts: List[str]
    ground_truth: str
    faithfulness: float = 0.0
    answer_relevance: float = 0.0
    context_precision: float = 0.0
    bleu_1: float = 0.0
    bleu_4: float = 0.0
    rouge_1_f: float = 0.0
    rouge_l_f: float = 0.0
    correlation_id: str = ""

    def to_dict(self) -> dict:
        """Convert to API-friendly dict with truncated fields."""
        return {
            "query": self.query[:80] + ("..." if len(self.query) > 80 else ""),
            "faithfulness": round(self.faithfulness, 4),
            "answer_relevance": round(self.answer_relevance, 4),
            "context_precision": round(self.context_precision, 4),
            "bleu_1": round(self.bleu_1, 4),
            "bleu_4": round(self.bleu_4, 4),
            "rouge_1_f": round(self.rouge_1_f, 4),
            "rouge_l_f": round(self.rouge_l_f, 4),
            "correlation_id": self.correlation_id,
        }


@dataclass
class RAGEvalSuite:
    """Aggregated metrics for a RAG evaluation dataset."""

    results: List[RAGASMetrics] = field(default_factory=list)
    correlation_id: str = ""

    def add(self, result: RAGASMetrics):
        """Add a single evaluation result to the suite."""
        self.results.append(result)

    def mean(self, attr: str) -> float:
        """Compute mean of a metric attribute across all results."""
        vals = [getattr(r, attr, 0.0) for r in self.results]
        return float(np.mean(vals)) if vals else 0.0

    def summary(self) -> dict:
        """Return aggregated metrics summary for reporting."""
        return {
            "n_queries": len(self.results),
            "mean_faithfulness": round(self.mean("faithfulness"), 4),
            "mean_answer_relevance": round(self.mean("answer_relevance"), 4),
            "mean_context_precision": round(self.mean("context_precision"), 4),
            "mean_bleu_1": round(self.mean("bleu_1"), 4),
            "mean_bleu_4": round(self.mean("bleu_4"), 4),
            "mean_rouge_1_f": round(self.mean("rouge_1_f"), 4),
            "mean_rouge_l_f": round(self.mean("rouge_l_f"), 4),
            "correlation_id": self.correlation_id,
        }


def _validate_eval_inputs(
    query: Optional[str],
    answer: Optional[str],
    contexts: Optional[List[str]],
    ground_truth: Optional[str],
    corr_id: str,
) -> tuple[bool, str]:
    """Validate evaluation inputs before processing."""
    if not isinstance(query, str) or not query.strip():
        return False, "query must be a non-empty string"
    if not isinstance(answer, str):
        return False, "answer must be a string"
    if not isinstance(contexts, list):
        return False, "contexts must be a list"
    if not isinstance(ground_truth, str):
        return False, "ground_truth must be a string"
    return True, ""


class RAGMetricsCalculator:
    """
    Computes RAG quality metrics: RAGAS-style + BLEU + ROUGE.

    Features:
    - Faithfulness: Are claims in answer supported by context?
    - Answer Relevance: Does answer address the query?
    - Context Precision: Are retrieved contexts actually useful?
    - BLEU/ROUGE: Standard NLP overlap metrics vs ground truth
    - PII scrubbing via centralized utils for all API prompts
    - Retry logic via centralized decorator for OpenAI calls
    - Correlation ID propagation for distributed tracing
    """

    def __init__(
        self,
        openai_api_key: Optional[str] = None,
        model: str = "gpt-4o",
        max_retries: int = 3,
        timeout_seconds: int = 30,
    ):
        settings = get_settings()
        self.api_key = openai_api_key or settings.openai_api_key
        self.model = model
        self.max_retries = max_retries
        self.timeout_seconds = timeout_seconds

        self.llm = get_llm(
            streaming=False,
            temperature_override=0.0,  # Evaluation needs deterministic output
        )

        self._embedder = CachedOpenAIEmbeddings(
            api_key=self.api_key,
            cache_dir=".cache/eval_embeddings",
        )

        _load_optional_eval_deps()

        # BLEU smoothing (None when nltk absent)
        if _SmoothingFunction is not None:
            self._smoother_bleu1 = _SmoothingFunction().method1
            self._smoother_bleu4 = _SmoothingFunction().method3
        else:
            self._smoother_bleu1 = None
            self._smoother_bleu4 = None

        # ROUGE with and without stemming (None when rouge_score package absent)
        if _rouge_scorer is not None:
            self._rouge_stemmed = _rouge_scorer.RougeScorer(["rouge1", "rougeL"], use_stemmer=True)
            self._rouge_unstemmed = _rouge_scorer.RougeScorer(["rouge1", "rougeL"], use_stemmer=False)
        else:
            self._rouge_stemmed = None
            self._rouge_unstemmed = None

        self._llm_retry = retry_async(
            config=RetryConfig(
                max_attempts=max_retries,
                backoff_base=0.5,
                exceptions=(Exception,),
            )
        )

        logger.info(f"RAGMetricsCalculator initialized: model={model}, retries={max_retries}")

    async def evaluate_sample(
        self,
        query: str,
        answer: str,
        contexts: List[str],
        ground_truth: str,
        correlation_id: Optional[str] = None,
    ) -> RAGASMetrics:
        """Evaluate a single RAG sample with all metrics."""
        corr_id = correlation_id or generate_eval_correlation_id("rag_metrics")

        # ✅ Validate inputs
        is_valid, error = _validate_eval_inputs(query, answer, contexts, ground_truth, corr_id)
        if not is_valid:
            logger.error(f"[{corr_id}] Invalid eval inputs: {error}")
            return RAGASMetrics(
                query=query or "",
                answer=answer or "",
                contexts=contexts or [],
                ground_truth=ground_truth or "",
                correlation_id=corr_id,
            )

        metrics = RAGASMetrics(
            query=query,
            answer=answer,
            contexts=contexts,
            ground_truth=ground_truth,
            correlation_id=corr_id,
        )

        # === BLEU scores (skipped if nltk unavailable) ===
        if _sentence_bleu is not None and self._smoother_bleu1 is not None:
            reference = ground_truth.lower().split()
            hypothesis = answer.lower().split()
            metrics.bleu_1 = _sentence_bleu(
                [reference],
                hypothesis,
                weights=(1, 0, 0, 0),
                smoothing_function=self._smoother_bleu1,
            )
            metrics.bleu_4 = _sentence_bleu(
                [reference],
                hypothesis,
                weights=(0.25, 0.25, 0.25, 0.25),
                smoothing_function=self._smoother_bleu4,
            )

        # === ROUGE scores (skipped if rouge_score unavailable) ===
        if self._rouge_stemmed is not None:
            rouge_scores = self._rouge_stemmed.score(ground_truth, answer)
            metrics.rouge_1_f = rouge_scores["rouge1"].fmeasure
            metrics.rouge_l_f = rouge_scores["rougeL"].fmeasure

        # === RAGAS-style LLM-evaluated metrics with timeout ===
        try:
            metrics.faithfulness = await asyncio.wait_for(
                self._eval_faithfulness(answer, contexts, corr_id),
                timeout=_METRIC_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning(f"[{corr_id}] Faithfulness eval timed out")
            metrics.faithfulness = 0.5

        try:
            metrics.answer_relevance = await asyncio.wait_for(
                self._eval_answer_relevance(query, answer, corr_id),
                timeout=_METRIC_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning(f"[{corr_id}] Answer relevance eval timed out")
            metrics.answer_relevance = 0.5

        try:
            metrics.context_precision = await asyncio.wait_for(
                self._eval_context_precision(query, answer, contexts, corr_id),
                timeout=_METRIC_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning(f"[{corr_id}] Context precision eval timed out")
            metrics.context_precision = 0.5

        return metrics

    async def evaluate_dataset(
        self,
        dataset: List[dict],
        rag_fn,
        correlation_id: Optional[str] = None,
    ) -> RAGEvalSuite:
        """Evaluate a full dataset using the provided RAG function."""
        corr_id = correlation_id or generate_eval_correlation_id("rag_dataset")
        suite = RAGEvalSuite(correlation_id=corr_id)

        for i, item in enumerate(dataset):
            logger.info(f"[{corr_id}] Evaluating [{i+1}/{len(dataset)}]: {item.get('query', '')[:60]}")
            try:
                answer, contexts = rag_fn(item["query"])
                result = await self.evaluate_sample(
                    query=item["query"],
                    answer=answer,
                    contexts=contexts,
                    ground_truth=item["ground_truth"],
                    correlation_id=corr_id,
                )
                suite.add(result)
            except Exception as e:
                logger.error(f"[{corr_id}] Failed to evaluate sample {i+1}: {e}")
                # Add placeholder result to keep dataset aligned
                suite.add(
                    RAGASMetrics(
                        query=item.get("query", ""),
                        answer="",
                        contexts=[],
                        ground_truth=item.get("ground_truth", ""),
                        correlation_id=corr_id,
                    )
                )
        return suite

    async def _eval_faithfulness(self, answer: str, contexts: List[str], corr_id: str) -> float:
        """Evaluate if answer claims are supported by retrieved contexts."""
        context_str = "\n\n".join(scrub_pii_for_evaluation(c, domain="all") for c in contexts[:3])
        scrubbed_answer = scrub_pii_for_evaluation(answer, domain="all")

        prompt = f"""Given this context:
{context_str}

And this answer:
{scrubbed_answer}

For each factual claim in the answer, determine if it is supported by the context.
Return JSON only:
{{
  "claims": [{{"claim": "...", "supported": true}}],
  "supported_count": 2,
  "total_count": 3,
  "faithfulness_score": 0.67
}}"""

        data = await call_llm_with_retry(
            prompt=prompt,
            model=self.model,
            max_tokens=600,
            temperature=0.0,
            response_format={"type": "json_object"},
            extract_key="faithfulness_score",
            default_value=0.5,
            correlation_id=corr_id,
        )

        if isinstance(data, (int, float)):
            return max(0.0, min(1.0, float(data)))
        # Handle dict response with faithfulness_score key
        if isinstance(data, dict):
            score = data.get("faithfulness_score")
            if isinstance(score, (int, float)):
                return max(0.0, min(1.0, float(score)))
        return 0.5

    async def _eval_answer_relevance(self, query: str, answer: str, corr_id: str) -> float:
        """Evaluate if answer is relevant to the query."""
        scrubbed_answer = scrub_pii_for_evaluation(answer, domain="all")

        prompt = f"""Generate 3 questions that this answer is trying to answer.
Answer: {scrubbed_answer}
Return only the 3 questions, one per line."""

        try:
            content = await call_llm_with_retry(
                prompt=prompt,
                model=self.model,
                max_tokens=200,
                temperature=0.3,
                correlation_id=corr_id,
            )

            if not content or not isinstance(content, str):
                return 0.5

            generated_questions = [q.strip() for q in content.strip().split("\n") if q.strip()][:3]

            if not generated_questions:
                return 0.5

            async def _get_embeddings():
                if inspect.iscoroutinefunction(self._embedder.embed_query):
                    query_vec = await self._embedder.embed_query(query)
                    gen_vecs = await self._embedder.embed_documents(generated_questions)
                else:
                    # Run sync embed in thread
                    import sys

                    if sys.version_info >= (3, 9):
                        query_vec = await asyncio.to_thread(lambda: self._embedder.embed_query(query))
                        gen_vecs = await asyncio.to_thread(lambda: self._embedder.embed_documents(generated_questions))
                    else:
                        loop = asyncio.get_running_loop()  # FIXED: get_event_loop() deprecated in Python 3.10+
                        query_vec = await loop.run_in_executor(None, lambda: self._embedder.embed_query(query))
                        gen_vecs = await loop.run_in_executor(
                            None,
                            lambda: self._embedder.embed_documents(generated_questions),
                        )
                return np.array(query_vec), np.array(gen_vecs)

            query_vec, gen_vecs = await _get_embeddings()

            query_norm = np.linalg.norm(query_vec)
            if query_norm < 1e-6:
                return 0.5

            gen_norms = np.linalg.norm(gen_vecs, axis=1)
            gen_norms = np.where(gen_norms < 1e-6, 1e-6, gen_norms)

            sims = np.dot(gen_vecs, query_vec) / (gen_norms * query_norm)
            sims = np.clip(sims, 0.0, 1.0)
            return float(np.mean(sims))

        except Exception as e:
            logger.warning(f"[{corr_id}] Answer relevance eval failed: {e}")
            return 0.5

    async def _eval_context_precision(self, query: str, answer: str, contexts: List[str], corr_id: str) -> float:
        """Evaluate if retrieved contexts are useful for answering the query."""
        if not contexts or not answer:
            return 0.0

        contexts_formatted = "\n\n".join(
            f"Context {i+1}: {scrub_pii_for_evaluation(ctx[:400], domain='all')}" for i, ctx in enumerate(contexts[:5])
        )

        prompt = f"""Question: {query}

{contexts_formatted}

For each context, determine if it is useful for answering the question.
Return JSON only:
{{
  "evaluations": [{{"context_num": 1, "useful": true}}],
  "useful_count": 1,
  "total_count": 2,
  "precision_score": 0.5
}}"""

        data = await call_llm_with_retry(
            prompt=prompt,
            model=self.model,
            max_tokens=600,
            temperature=0.0,
            response_format={"type": "json_object"},
            extract_key="precision_score",
            default_value=0.5,
            correlation_id=corr_id,
        )

        if isinstance(data, (int, float)):
            return max(0.0, min(1.0, float(data)))
        # Handle dict response with precision_score key
        if isinstance(data, dict):
            score = data.get("precision_score")
            if isinstance(score, (int, float)):
                return max(0.0, min(1.0, float(score)))
        return 0.5

    @staticmethod
    def _scrub_pii_for_api(text: str) -> str:
        """DEPRECATED: Use app.core.pii_utils.scrub_pii_for_evaluation instead."""
        import warnings

        warnings.warn(
            "RAGMetricsCalculator._scrub_pii_for_api is deprecated. "
            "Use app.core.pii_utils.scrub_pii_for_evaluation instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return scrub_pii_for_evaluation(text, domain="all")


def get_rag_metrics_metadata() -> dict[str, Any]:
    """✅ NEW: Return RAG metrics metadata for monitoring."""
    return {
        "model": get_settings().openai_chat_model,
        "metrics": [
            "faithfulness",
            "answer_relevance",
            "context_precision",
            "bleu_1",
            "bleu_4",
            "rouge_1_f",
            "rouge_l_f",
        ],
        "timeout_per_metric": _METRIC_TIMEOUT,
        "retry_config": {
            "max_attempts": 3,
            "backoff_base": 0.5,
        },
        "bleu_smoothing": {
            "bleu_1": "method1",
            "bleu_4": "method3",
        },
        "rouge_config": {
            "metrics": ["rouge1", "rougeL"],
            "stemming": True,
        },
    }


# DVMELTSS-M: Explicit module exports
__all__ = [
    "RAGMetricsCalculator",
    "RAGASMetrics",
    "RAGEvalSuite",
    "get_rag_metrics_metadata",
]
# Local smoke test entry point. Run: python -m

