
from __future__ import annotations

import asyncio
import logging
import smtplib
from dataclasses import dataclass
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Final, Optional

# DVMELTSS-M: Import centralized utilities
from app.config import get_settings
from app.core.retry import RetryConfig
from app.core.pii_utils import scrub_pii_for_evaluation

logger = logging.getLogger(__name__)

ALERT_LOG_PATH: Final = Path(".cache/alerts/alert_history.log")


@dataclass
class Alert:
    """A single evaluation alert."""

    metric: str
    value: float
    threshold: float
    domain: str
    timestamp: str
    message: str
    severity: str  # "warning" | "critical"
    correlation_id: str = ""  # FIXED: Added for tracing

    @property
    def is_critical(self) -> bool:
        return self.severity == "critical"


class AlertEngine:
    """
    Sends alerts when RAGAs metrics drop below thresholds.

    Alert channels:
    1. Log file — always active, no config needed
    2. Email — requires SMTP settings in .env
    3. Console — always active
    """

    DEFAULT_THRESHOLDS: Final = {
        "faithfulness": {"warning": 0.75, "critical": 0.60},
        "answer_relevancy": {"warning": 0.65, "critical": 0.50},
        "context_precision": {"warning": 0.60, "critical": 0.45},
        "context_recall": {"warning": 0.55, "critical": 0.40},
    }

    def __init__(self, thresholds: Optional[dict] = None):
        settings = get_settings()
        self.settings = settings
        self.thresholds = thresholds or self.DEFAULT_THRESHOLDS

        ALERT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

        self._email_retry_config = RetryConfig(
            max_attempts=3,
            backoff_base=1.0,
            exceptions=(smtplib.SMTPException, ConnectionError, OSError),
        )

    def check_and_send(
        self,
        metrics: dict[str, float],
        domain: str,
        run_id: str = "",
        correlation_id: Optional[str] = None,  # FIXED: Added param
    ) -> list[Alert]:
        """Check all metrics against thresholds and send alerts if needed."""
        corr_id = correlation_id or "alert_unknown"
        triggered: list[Alert] = []
        now = datetime.now(timezone.utc).isoformat()

        metric_map = {
            "mean_faithfulness": "faithfulness",
            "mean_answer_relevancy": "answer_relevancy",
            "mean_context_precision": "context_precision",
            "mean_context_recall": "context_recall",
        }

        for metric_key, threshold_key in metric_map.items():
            value = metrics.get(metric_key, 1.0)
            thresholds = self.thresholds.get(threshold_key, {})

            severity = None
            threshold = None

            if value < thresholds.get("critical", 0.0):
                severity = "critical"
                threshold = thresholds["critical"]
            elif value < thresholds.get("warning", 0.0):
                severity = "warning"
                threshold = thresholds["warning"]

            if severity:
                safe_message = scrub_pii_for_evaluation(
                    f"[{severity.upper()}] RAGAs {threshold_key} for '{domain}' "
                    f"dropped to {value:.3f} (threshold: {threshold}) | "
                    f"run_id: {run_id}",
                    domain="all",
                )

                alert = Alert(
                    metric=threshold_key,
                    value=round(value, 4),
                    threshold=threshold,
                    domain=domain,
                    timestamp=now,
                    message=safe_message,
                    severity=severity,
                    correlation_id=corr_id,  # FIXED: Propagate correlation_id
                )
                triggered.append(alert)
                self._log_alert(alert)
                import asyncio

                asyncio.create_task(self._send_email_alert(alert))

        if not triggered:
            logger.info(f"[{corr_id}] AlertEngine: all metrics within thresholds for {domain}")

        return triggered

    def _log_alert(self, alert: Alert):
        """Write alert to log file."""
        line = (
            f"{alert.timestamp} | {alert.correlation_id} | {alert.severity.upper()} | "
            f"{alert.domain} | {alert.metric} | "
            f"{alert.value} < {alert.threshold}\n"
        )
        try:
            with open(ALERT_LOG_PATH, "a") as f:
                f.write(line)
        except OSError as e:
            logger.warning(f"Alert log write failed: {e}")

        if alert.is_critical:
            logger.critical(f"[{alert.correlation_id}] {alert.message}")
        else:
            logger.warning(f"[{alert.correlation_id}] {alert.message}")

    async def _send_email_alert(self, alert: Alert):
        """Send email alert via SMTP with inline retry logic."""
        smtp_host = getattr(self.settings, "alert_smtp_host", "")
        smtp_user = getattr(self.settings, "alert_smtp_user", "")
        smtp_pass = getattr(self.settings, "alert_smtp_pass", "")
        to_email = getattr(self.settings, "alert_email_to", "")

        if not all([smtp_host, smtp_user, smtp_pass, to_email]):
            return  # email not configured — skip silently

        cfg = self._email_retry_config
        last_error: Optional[Exception] = None
        for attempt in range(cfg.max_attempts):
            try:
                await asyncio.to_thread(
                    self._do_send_email,
                    alert,
                    smtp_host,
                    smtp_user,
                    smtp_pass,
                    to_email,
                )
                logger.info(f"[{alert.correlation_id}] Alert email sent to {to_email}")
                return
            except cfg.exceptions as e:
                last_error = e
                if attempt < cfg.max_attempts - 1:
                    import random

                    delay = min(cfg.backoff_base * (2**attempt), 5.0)
                    await asyncio.sleep(delay * (0.5 + random.random()))
        logger.warning(f"[{alert.correlation_id}] Alert email failed after {cfg.max_attempts} retries: {last_error}")

    def _do_send_email(
        self,
        alert: Alert,
        smtp_host: str,
        smtp_user: str,
        smtp_pass: str,
        to_email: str,
    ):
        """Actual SMTP sending logic (called by retry decorator)."""
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[DocuMind AI] {alert.severity.upper()} — " f"{alert.metric} dropped to {alert.value:.3f}"
        msg["From"] = smtp_user
        msg["To"] = to_email

        safe_message = scrub_pii_for_evaluation(alert.message, domain="all")

        html_body = f"""
<h2>DocuMind AI RAGAs Alert</h2>
<table border="1" cellpadding="6" style="border-collapse:collapse">
  <tr><th>Metric</th><td>{alert.metric}</td></tr>
  <tr><th>Value</th><td style="color:red">{alert.value:.4f}</td></tr>
  <tr><th>Threshold</th><td>{alert.threshold}</td></tr>
  <tr><th>Domain</th><td>{alert.domain}</td></tr>
  <tr><th>Severity</th><td>{alert.severity}</td></tr>
  <tr><th>Time</th><td>{alert.timestamp}</td></tr>
  <tr><th>Correlation ID</th><td>{alert.correlation_id}</td></tr>
</table>
<p>{safe_message}</p>
<p>Check MLflow for full evaluation report.</p>
"""
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP_SSL(smtp_host, 465) as server:
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, to_email, msg.as_string())

    def get_alert_history(self, last_n: int = 50) -> list[str]:
        """Read recent alert history from log file."""
        if not ALERT_LOG_PATH.exists():
            return []
        try:
            lines = ALERT_LOG_PATH.read_text().splitlines()
            return lines[-last_n:]
        except OSError:
            return []


# DVMELTSS-M: Explicit module exports
__all__ = ["AlertEngine", "Alert"]
# Local smoke test entry point. Run: python -m

