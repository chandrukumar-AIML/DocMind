import asyncio
from app.evaluation.retrieval_metrics import RetrievalEvaluator
from app.evaluation.ocr_metrics import OCRMetricsCalculator
from app.evaluation.text_utils import tokenize_for_wer


def test_tokenize_edge_cases():
    """Verify tokenize handles punctuation-only and empty inputs."""
    assert tokenize_for_wer("") == []
    assert tokenize_for_wer("...") == ["..."]  # Punctuation-only
    assert tokenize_for_wer("Hello, world!") == ["Hello", ",", "world", "!"]
    assert tokenize_for_wer("don't") == ["don't"]  # Contraction preserved


def test_windowed_cer_empty_chunk():
    """Verify windowed CER handles empty ground truth chunks."""
    calc = OCRMetricsCalculator()
    # Should not raise division by zero
    cer = calc._windowed_cer("predicted", "", window=100)
    assert cer == 0.0


def test_async_safe_timeout():
    """Verify retrieval evaluator works in async context."""
    import time
    from app.evaluation.retrieval_metrics import RetrievalEvalSuite

    def slow_retrieve(query: str, k: int):
        time.sleep(0.1)  # Simulate work
        return []

    evaluator = RetrievalEvaluator()
    ground_truth = [{"query": "test", "relevant_chunk_ids": {"id1"}}]

    # Run in async context (like FastAPI would)
    async def run_eval():
        return await evaluator.evaluate(ground_truth, slow_retrieve, k=3, timeout_seconds=1)

    # Should complete without signal errors
    result = asyncio.run(run_eval())
    assert isinstance(result, RetrievalEvalSuite)


def test_bootstrap_zero_variance():
    """Verify bootstrap CI handles constant values."""
    from app.evaluation.retrieval_metrics import RetrievalEvalSuite
    import numpy as np

    vals = np.array([0.5, 0.5, 0.5, 0.5, 0.5])  # Zero variance
    ci_lower, ci_upper = RetrievalEvalSuite._bootstrap_ci(vals, n_bootstrap=100)
    # Should return identical bounds for constant input
    assert ci_lower == ci_upper == 0.5
