# backend/app/evaluation/langsmith_dataset.py
# DVMELTSS-FIX: V - Validate, E - Error handling, M - Modular, S - Security
# ASCALE-FIX: S - Separation, C - Coupling
# OWASP-FIX: 7 - PII protection for ground truth storage
# ✅ FIXED: Proper sync retry + safe arg passing + input validation

from __future__ import annotations

import asyncio
import functools
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Final, List, Optional

# DVMELTSS-M: Import centralized utilities
from app.config import get_settings
from app.core.pii_utils import scrub_pii_for_evaluation
from app.core.retry import retry_async, RetryConfig

logger = logging.getLogger(__name__)

# Required keys for every evaluation example
REQUIRED_EXAMPLE_KEYS: Final = frozenset({"question", "ground_truth"})

# DVMELTSS-E: Retry config for LangSmith API calls
_LANGSMITH_RETRY_CONFIG: Final = RetryConfig(
    max_attempts=3,
    backoff_base=1.0,
    backoff_max=10.0,
    exceptions=(Exception,),
)

# ✅ NEW: Timeout for LangSmith API calls (seconds)
_LANGSMITH_TIMEOUT: Final = 60.0


@dataclass
class EvalRunResult:
    """Result of a LangSmith evaluation run."""
    success: bool
    results: Dict[str, Any]
    error: Optional[str] = None
    experiment_url: Optional[str] = None
    correlation_id: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to API-friendly dict."""
        return {
            "success": self.success,
            "error": self.error,
            "experiment_url": self.experiment_url,
            "correlation_id": self.correlation_id,
            "results_summary": {
                k: v for k, v in self.results.items() 
                if k in ["accuracy", "mean_score", "n_examples"]
            } if self.results else {},
        }


# ✅ NEW: Input validation helper
def _validate_dataset_inputs(
    examples: Optional[List[Dict[str, Any]]],
    batch_size: int,
    corr_id: str,
) -> tuple[bool, str]:
    """Validate dataset inputs before processing."""
    if examples is not None and not isinstance(examples, list):
        return False, "examples must be a list or None"
    if not isinstance(batch_size, int) or batch_size <= 0:
        return False, "batch_size must be a positive integer"
    return True, ""


class LangSmithEvalDataset:
    """
    Helper for building and managing LangSmith evaluation datasets.
    
    Features:
    - Create/get datasets with idempotent behavior
    - Batch upload examples with validation
    - Async support for non-blocking uploads
    - Centralized PII scrubbing for ground truth storage
    - Graceful degradation when LangSmith unavailable
    - Correlation ID tracing for distributed debugging
    """

    def __init__(self, dataset_name: str = "documind-eval"):
        self.dataset_name = dataset_name
        self._client = None
        self._dataset = None
        self._setup()

    def _setup(self):
        """Initialize LangSmith client with error handling."""
        try:
            from langsmith import Client
            settings = get_settings()
            
            # Only initialize if API key is present
            if not settings.langchain_api_key:
                logger.debug("LANGCHAIN_API_KEY not set — skipping LangSmith client init")
                return
                
            self._client = Client(api_key=settings.langchain_api_key, api_url=settings.langchain_endpoint)
            logger.info(f"LangSmith client connected: {self.dataset_name}")
        except ImportError:
            logger.warning("langsmith package not installed — evaluation features disabled")
        except Exception as e:
            logger.warning(f"LangSmith client setup failed: {e}")

    def create_or_get_dataset(self, correlation_id: Optional[str] = None) -> bool:
        """
        Create dataset if not exists, or get existing one.
        
        Args:
            correlation_id: Optional request ID for tracing context
            
        Returns:
            True if dataset is ready, False if client unavailable
        """
        corr_id = correlation_id or "dataset_init"
        if not self._client:
            logger.debug(f"[{corr_id}] LangSmith client not available")
            return False
            
        try:
            # ✅ FIXED: Safe list conversion for generator
            datasets = list(self._client.list_datasets(dataset_name=self.dataset_name))
            
            if datasets:
                self._dataset = datasets[0]
                logger.info(f"[{corr_id}] Using existing dataset: {self.dataset_name} (id={self._dataset.id})")
            else:
                # FIXED: Use centralized metadata helper
                from app.observability.langsmith_config import get_dataset_metadata
                metadata = get_dataset_metadata(self.dataset_name, correlation_id=corr_id)
                
                self._dataset = self._client.create_dataset(
                    dataset_name=self.dataset_name,
                    description=metadata["description"],
                    metadata=metadata["metadata"],
                )
                logger.info(f"[{corr_id}] Created new dataset: {self.dataset_name} (id={self._dataset.id})")
            return True
        except Exception as e:
            logger.error(f"[{corr_id}] Dataset create/get failed: {e}")
            return False

    def add_examples(self, examples: List[Dict[str, Any]], batch_size: int = 100, correlation_id: Optional[str] = None) -> int:
        """
        Add evaluation examples to the dataset.
        
        Args:
            examples: List of dicts with 'question' and 'ground_truth' keys
            batch_size: Number of examples per API call
            correlation_id: Request ID for distributed tracing
            
        Returns:
            Number of examples successfully added
            
        Raises:
            ValueError: If any example is missing required keys
        """
        corr_id = correlation_id or "dataset_upload"
        
        # ✅ Validate inputs
        is_valid, error = _validate_dataset_inputs(examples, batch_size, corr_id)
        if not is_valid:
            logger.error(f"[{corr_id}] Invalid inputs: {error}")
            return 0
        
        if not examples:
            return 0

        if not self._client or not self._dataset:
            logger.warning(f"[{corr_id}] LangSmith dataset not initialized.")
            return 0

        # Validate all examples BEFORE sending any (fail-fast)
        invalid = []
        for i, ex in enumerate(examples):
            missing = REQUIRED_EXAMPLE_KEYS - set(ex.keys())
            if missing:
                invalid.append(f"Example {i} missing keys: {sorted(missing)}")
            # Optional: validate ground_truth is non-empty
            if not ex.get("ground_truth", "").strip():
                invalid.append(f"Example {i} has empty ground_truth")
                
        if invalid:
            raise ValueError("Invalid examples:\n" + "\n".join(invalid[:10]))  # Show first 10 errors

        # Prepare inputs/outputs with centralized PII scrubbing
        inputs = []
        outputs = []
        for ex in examples:
            inputs.append({
                "question": ex["question"],
                "filter_dict": ex.get("filter_dict", {}),
            })
            # FIXED: Use centralized PII scrubbing
            outputs.append({
                "answer": scrub_pii_for_evaluation(ex["ground_truth"], domain="general"),
                "source_files": ex.get("source_files", []),
                "document_type": ex.get("document_type", "unknown"),
            })

        # Upload in batches with error handling per batch
        total_added = 0
        for i in range(0, len(inputs), batch_size):
            batch_inputs = inputs[i: i + batch_size]
            batch_outputs = outputs[i: i + batch_size]
            try:
                # ✅ FIXED: Use sync retry wrapper (not async decorator)
                def _do_upload():
                    return self._client.create_examples(
                        inputs=batch_inputs,
                        outputs=batch_outputs,
                        dataset_id=self._dataset.id,
                    )
                
                # Simple retry loop for sync API call
                last_error = None
                for attempt in range(_LANGSMITH_RETRY_CONFIG.max_attempts):
                    try:
                        _do_upload()
                        total_added += len(batch_inputs)
                        logger.debug(f"[{corr_id}] Uploaded batch {i // batch_size + 1}: {len(batch_inputs)} examples")
                        break
                    except Exception as e:
                        last_error = e
                        if attempt < _LANGSMITH_RETRY_CONFIG.max_attempts - 1:
                            # Exponential backoff
                            wait = min(
                                _LANGSMITH_RETRY_CONFIG.backoff_base * (2 ** attempt),
                                _LANGSMITH_RETRY_CONFIG.backoff_max
                            )
                            time.sleep(wait)
                            continue
                        # All retries exhausted
                        raise
                
            except Exception as e:
                # Log which examples failed for debugging
                failed_indices = list(range(i, min(i + batch_size, len(inputs))))
                logger.error(
                    f"[{corr_id}] Batch {i // batch_size + 1} (examples {failed_indices}) upload failed: {e}"
                )
                # Continue with next batch rather than failing entire upload
                continue
             

        logger.info(f"[{corr_id}] Added {total_added}/{len(examples)} examples to {self.dataset_name}")
        return total_added

    async def add_examples_async(self, examples: List[Dict[str, Any]], batch_size: int = 100, correlation_id: Optional[str] = None) -> int:
        """
        Async version of add_examples for non-blocking uploads.
        
        Args:
            examples: List of evaluation examples
            batch_size: Examples per batch
            correlation_id: Request ID for distributed tracing
            
        Returns:
            Number of examples added
        """
        # ✅ FIXED: Use functools.partial for safe arg passing to run_in_executor
        func = functools.partial(
            self.add_examples,
            examples=examples,
            batch_size=batch_size,
            correlation_id=correlation_id,
        )
        loop = asyncio.get_running_loop()  # FIXED: get_event_loop() deprecated in Python 3.10+
        return await loop.run_in_executor(None, func)

    def run_evaluation(
        self,
        rag_chain_fn: Callable[[str], Any],
        evaluators: List[Any],
        experiment_prefix: str = "documind",
        metadata: Optional[Dict[str, str]] = None,
        correlation_id: Optional[str] = None,
    ) -> EvalRunResult:
        """
        Run LangSmith evaluation against the dataset.
        
        Args:
            rag_chain_fn: Function that takes question and returns answer + contexts
            evaluators: List of LangChain evaluator objects
            experiment_prefix: Prefix for experiment name
            metadata: Additional metadata for the experiment
            correlation_id: Request ID for distributed tracing
            
        Returns:
            EvalRunResult with success status and results
        """
        corr_id = correlation_id or "eval_run"
        
        if not evaluators:
            raise ValueError("evaluators list cannot be empty.")
        if not self._client or not self._dataset:
            return EvalRunResult(
                success=False, 
                results={}, 
                error="LangSmith dataset not initialized",
                correlation_id=corr_id,
            )

        try:
            from langsmith.evaluation import evaluate
            settings = get_settings()
            
            # FIXED: Merge correlation_id into metadata
            eval_metadata = {**(metadata or {}), "version": settings.app_version}
            if correlation_id:
                eval_metadata["correlation_id"] = correlation_id
            
            # Run evaluation
            results = evaluate(
                rag_chain_fn,
                data=self.dataset_name,
                evaluators=evaluators,
                experiment_prefix=experiment_prefix,
                metadata=eval_metadata,
            )
            
            # ✅ FIXED: Safe attribute access for different result types
            experiment_url = None
            if results:
                exp_id = getattr(results, "experiment_id", None)
                if exp_id:
                    experiment_url = (
                        f"{settings.langchain_endpoint.rstrip('/')}/o/"
                        f"{settings.langchain_project}/datasets/"
                        f"{self._dataset.id}/compare?selectedSessions={exp_id}"
                    )
            
            # ✅ FIXED: Safe results extraction
            n_examples = 0
            if hasattr(results, "results") and results.results:
                n_examples = len(results.results)
            elif hasattr(results, "__len__"):
                n_examples = len(results)
            
            return EvalRunResult(
                success=True,
                results={"n_examples": n_examples},
                experiment_url=experiment_url,
                correlation_id=corr_id,
            )
        except ImportError:
            return EvalRunResult(
                success=False,
                results={},
                error="langsmith.evaluation module not available",
                correlation_id=corr_id,
            )
        except Exception as e:
            logger.error(f"[{corr_id}] LangSmith evaluation failed: {e}")
            return EvalRunResult(success=False, results={}, error=str(e), correlation_id=corr_id)

    @staticmethod
    def _scrub_pii(text: str) -> str:
        """
        DEPRECATED: Use app.core.pii_utils.scrub_pii_for_evaluation instead.
        Basic PII scrubbing for ground truth storage.
        """
        import warnings
        warnings.warn(
            "LangSmithEvalDataset._scrub_pii is deprecated. "
            "Use app.core.pii_utils.scrub_pii_for_evaluation instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return scrub_pii_for_evaluation(text, domain="general")


def get_langsmith_dataset_metadata() -> dict[str, Any]:
    """✅ NEW: Return LangSmith dataset metadata for debugging."""
    return {
        "required_example_keys": list(REQUIRED_EXAMPLE_KEYS),
        "retry_config": {
            "max_attempts": _LANGSMITH_RETRY_CONFIG.max_attempts,
            "backoff_base": _LANGSMITH_RETRY_CONFIG.backoff_base,
            "backoff_max": _LANGSMITH_RETRY_CONFIG.backoff_max,
        },
        "api_timeout_seconds": _LANGSMITH_TIMEOUT,
        "default_batch_size": 100,
    }


# DVMELTSS-M: Explicit module exports
__all__ = [
    "LangSmithEvalDataset",
    "EvalRunResult",
    "get_langsmith_dataset_metadata",
]
# Local smoke test entry point. Run: python -m 
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)

