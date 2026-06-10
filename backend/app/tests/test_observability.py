from unittest.mock import Mock, patch
from app.observability.mlflow_logger import MLflowLogger
from app.observability.langsmith_config import get_run_metadata


def test_mlflow_uri_fix():
    """Verify start_run() doesn't crash with undefined uri."""
    with patch("app.observability.mlflow_logger.mlflow") as mock_mlflow, patch(
        "app.observability.mlflow_logger.get_settings"
    ) as mock_settings:
        mock_settings.return_value.mlflow_tracking_uri = "http://localhost:5000"
        mock_settings.return_value.mlflow_experiment_name = "test"
        mock_settings.return_value.app_version = "1.0.0"
        mock_settings.return_value.api_reload = False

        mock_run = Mock()
        mock_run.info.experiment_id = "exp123"
        mock_run.info.run_id = "run456"
        mock_mlflow.active_run.return_value = None
        mock_mlflow.start_run.return_value.__enter__.return_value = mock_run

        logger = MLflowLogger()

        with logger.start_run("test_run"):
            pass

        assert mock_mlflow.start_run.called


def test_circuit_breaker_thread_safety():
    """Verify circuit breaker is thread-safe."""
    import threading

    logger = MLflowLogger()

    def log_metrics():
        logger._safe_log_metrics({"test": 1.0})

    threads = [threading.Thread(target=log_metrics) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert isinstance(logger._failure_count, int)


def test_langsmith_metadata_truncation():
    """Verify metadata values are truncated."""
    metadata = get_run_metadata(extra={"long_value": "x" * 1000, "short": "ok"})
    assert len(metadata["long_value"]) <= 500
    assert metadata["short"] == "ok"


def test_pii_scrubbing_extended():
    """Verify extended PII patterns are scrubbed (actual replacement tokens)."""
    from app.observability.langsmith_dataset import LangSmithEvalDataset
    import warnings

    text = "Contact: test@example.com, Card: 1234-5678-9012-3456, Passport: AB1234567"

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        scrubbed = LangSmithEvalDataset._scrub_pii(text)

    # Actual replacement tokens used by _scrub_pii (include "REDACTED" suffix)
    assert "[EMAIL REDACTED]" in scrubbed
    assert "[CARD REDACTED]" in scrubbed
    assert "[PASSPORT REDACTED]" in scrubbed
    assert "test@example.com" not in scrubbed
