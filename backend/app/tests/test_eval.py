import asyncio
from app.evaluation.retrieval_metrics import RetrievalEvaluator, RetrievalEvalSuite
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
    # Should not crash division by zero
    cer = calc._windowed_cer("predicted", "", window=100)
    assert cer == 0.0


def test_async_safe_timeout():
    """Verify retrieval evaluator works in async context."""
    import time

    def slow_retrieve(query: str, k: int):
        time.sleep(0.1)  # Simulate work
        return []

    evaluator = RetrievalEvaluator()
    ground_truth = [{"query": "test", "relevant_chunk_ids": {"id1"}}]

    async def run_eval():
        return await evaluator.evaluate(ground_truth, slow_retrieve, k=3, timeout_seconds=1)

    result = asyncio.run(run_eval())
    assert isinstance(result, RetrievalEvalSuite)


def test_eval_suite_constant_values():
    """Verify RetrievalEvalSuite aggregates results correctly."""
    from app.evaluation.retrieval_metrics import RetrievalResult

    suite = RetrievalEvalSuite()
    # All 5 results retrieve exactly 1 relevant doc out of k=2
    for _ in range(5):
        suite.add(
            RetrievalResult(
                query="test",
                retrieved_ids=["id1", "id2"],  # id1 relevant, id2 not
                relevant_ids={"id1"},
                k=2,
            )
        )

    # precision@2 = 1 hit / 2 retrieved = 0.5 for all results → mean = 0.5
    assert suite.mean_precision_at_k == 0.5
    # recall@2 = 1 hit / 1 relevant = 1.0 for all results → mean = 1.0
    assert suite.mean_recall_at_k == 1.0
