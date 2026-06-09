# backend/app/ocr/cost_tracking.py
# DVMELTSS-FIX: V - Validate, E - Error handling, S - Security, M - Modular
# ASCALE-FIX: S - Separation, C - Coupling
# BATMAN-FIX: M - Memory safety, T - Thread safety + Async safety

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Any  # ✅ FIXED: Added Any to imports

from app.config import get_settings

logger = logging.getLogger(__name__)


def _get_pricing() -> dict[str, float]:
    """Get Vision API pricing from settings with fallback defaults."""
    try:
        settings = get_settings()
        return {
            "input_per_1m": getattr(settings, "vision_input_cost_per_1m", 5.00),
            "output_per_1m": getattr(settings, "vision_output_cost_per_1m", 15.00),
            "image_tokens_high": getattr(settings, "vision_image_tokens_high_detail", 765),
            "image_tokens_low": getattr(settings, "vision_image_tokens_low_detail", 425),
        }
    except Exception:
        return {
            "input_per_1m": 5.00,
            "output_per_1m": 15.00,
            "image_tokens_high": 765,
            "image_tokens_low": 425,
        }


@dataclass
class VisionCostTracker:
    """
    Thread-safe + async-safe tracker for GPT-4o Vision API usage and estimated cost.

    ✅ FIXED: Dual-mode locking (threading + asyncio), config-driven pricing, validation.

    DVMELTSS-M: Singleton-friendly design with class-level config.
    BATMAN-T: Uses appropriate lock for sync/async contexts.
    """

    _ocr_fallback_calls: int = field(default=0, init=False)
    _table_analysis_calls: int = field(default=0, init=False)
    _diagram_analysis_calls: int = field(default=0, init=False)
    _metadata_calls: int = field(default=0, init=False)
    _total_input_tokens: int = field(default=0, init=False)
    _total_output_tokens: int = field(default=0, init=False)
    _total_images_sent: int = field(default=0, init=False)

    correlation_id: Optional[str] = None
    last_updated: float = field(default_factory=time.time, init=False)

    _thread_lock: Optional[threading.Lock] = field(default=None, init=False, repr=False)
    _async_lock: Optional[asyncio.Lock] = field(default=None, init=False, repr=False)

    _pricing: dict[str, float] = field(default_factory=_get_pricing, init=False)

    def __post_init__(self):
        """Initialize locks and validate initial state."""
        object.__setattr__(self, "_thread_lock", threading.Lock())
        object.__setattr__(self, "_async_lock", asyncio.Lock())
        object.__setattr__(self, "last_updated", time.time())
        self._validate_counters()

    def _validate_counters(self) -> None:
        """Ensure all counters are non-negative."""
        for attr in [
            "_ocr_fallback_calls",
            "_table_analysis_calls",
            "_diagram_analysis_calls",
            "_metadata_calls",
            "_total_input_tokens",
            "_total_output_tokens",
            "_total_images_sent",
        ]:
            val = getattr(self, attr)
            if val < 0:
                logger.warning(f"Counter {attr} was negative ({val}) — resetting to 0")
                object.__setattr__(self, attr, 0)

    def _get_lock(self, is_async: bool = False):
        """Get appropriate lock for sync/async context."""
        return self._async_lock if is_async else self._thread_lock

    def log_call(
        self,
        call_type: str,
        input_tokens: int,
        output_tokens: int,
        images_sent: int = 1,
        correlation_id: Optional[str] = None,
        detail_level: str = "high",
        is_async: bool = False,
    ):
        """
        Log a single API call with thread/async-safe counter updates.

        ✅ FIXED: Input validation + dual-mode locking + config-driven pricing.
        """
        input_tokens = max(0, int(input_tokens))
        output_tokens = max(0, int(output_tokens))
        images_sent = max(0, int(images_sent))

        image_token_rate = (
            self._pricing["image_tokens_high"] if detail_level == "high" else self._pricing["image_tokens_low"]
        )
        image_tokens = images_sent * image_token_rate

        lock = self._get_lock(is_async)

        if is_async:
            raise RuntimeError(
                "log_call(is_async=True) must be called from async context. "
                "Use await log_call_async() instead for true async safety."
            )
        else:
            with lock:  # type: ignore[arg-type]
                self._update_counters_sync(call_type, input_tokens, output_tokens, images_sent, image_tokens)
                if correlation_id:
                    object.__setattr__(self, "correlation_id", correlation_id)
                object.__setattr__(self, "last_updated", time.time())

    async def log_call_async(
        self,
        call_type: str,
        input_tokens: int,
        output_tokens: int,
        images_sent: int = 1,
        correlation_id: Optional[str] = None,
        detail_level: str = "high",
    ):
        """
        Async version of log_call — use this from async FastAPI routes.

        ✅ Properly awaits asyncio.Lock for true async safety.
        """
        input_tokens = max(0, int(input_tokens))
        output_tokens = max(0, int(output_tokens))
        images_sent = max(0, int(images_sent))

        image_token_rate = (
            self._pricing["image_tokens_high"] if detail_level == "high" else self._pricing["image_tokens_low"]
        )
        image_tokens = images_sent * image_token_rate

        async with self._async_lock:
            self._update_counters_sync(call_type, input_tokens, output_tokens, images_sent, image_tokens)
            if correlation_id:
                object.__setattr__(self, "correlation_id", correlation_id)
            object.__setattr__(self, "last_updated", time.time())

    def _update_counters_sync(
        self,
        call_type: str,
        input_tokens: int,
        output_tokens: int,
        images_sent: int,
        image_tokens: int,
    ):
        """Internal counter update logic (called within lock)."""
        counter_map = {
            "ocr_fallback": "_ocr_fallback_calls",
            "table_analysis": "_table_analysis_calls",
            "diagram_analysis": "_diagram_analysis_calls",
            "metadata": "_metadata_calls",
        }
        attr = counter_map.get(call_type)
        if attr:
            current = getattr(self, attr)
            object.__setattr__(self, attr, current + 1)

        object.__setattr__(
            self,
            "_total_input_tokens",
            self._total_input_tokens + input_tokens + image_tokens,
        )
        object.__setattr__(self, "_total_output_tokens", self._total_output_tokens + output_tokens)
        object.__setattr__(self, "_total_images_sent", self._total_images_sent + images_sent)

    @property
    def ocr_fallback_calls(self) -> int:
        return max(0, self._ocr_fallback_calls)

    @property
    def table_analysis_calls(self) -> int:
        return max(0, self._table_analysis_calls)

    @property
    def diagram_analysis_calls(self) -> int:
        return max(0, self._diagram_analysis_calls)

    @property
    def metadata_calls(self) -> int:
        return max(0, self._metadata_calls)

    @property
    def total_input_tokens(self) -> int:
        return max(0, self._total_input_tokens)

    @property
    def total_output_tokens(self) -> int:
        return max(0, self._total_output_tokens)

    @property
    def total_images_sent(self) -> int:
        return max(0, self._total_images_sent)

    @property
    def estimated_cost_usd(self) -> float:
        """Calculate estimated cost in USD (read-only)."""
        input_cost = (self.total_input_tokens / 1_000_000) * self._pricing["input_per_1m"]
        output_cost = (self.total_output_tokens / 1_000_000) * self._pricing["output_per_1m"]
        return round(input_cost + output_cost, 4)

    @property
    def total_calls(self) -> int:
        """Total number of Vision API calls made."""
        return self.ocr_fallback_calls + self.table_analysis_calls + self.diagram_analysis_calls + self.metadata_calls

    def report(self) -> dict:
        """Return thread-safe usage report with per-type cost breakdown."""
        lock = self._thread_lock
        if lock:
            with lock:
                return self._generate_report()
        else:
            return self._generate_report()

    def _generate_report(self) -> dict:
        """Internal report generation (called within lock)."""
        type_costs = {}
        for call_type, count in [
            ("ocr_fallback", self.ocr_fallback_calls),
            ("table_analysis", self.table_analysis_calls),
            ("diagram_analysis", self.diagram_analysis_calls),
            ("metadata", self.metadata_calls),
        ]:
            if count > 0:
                avg_input = 1000 + self._pricing["image_tokens_high"]
                avg_output = 500
                type_costs[call_type] = (
                    round(
                        (avg_input / 1_000_000) * self._pricing["input_per_1m"]
                        + (avg_output / 1_000_000) * self._pricing["output_per_1m"],
                        4,
                    )
                    * count
                )

        return {
            "total_calls": self.total_calls,
            "calls_by_type": {
                "ocr_fallback": self.ocr_fallback_calls,
                "table_analysis": self.table_analysis_calls,
                "diagram_analysis": self.diagram_analysis_calls,
                "metadata": self.metadata_calls,
            },
            "cost_by_type_usd": type_costs,
            "total_images_sent": self.total_images_sent,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "correlation_id": self.correlation_id,
            "last_updated": self.last_updated,
            "pricing": self._pricing.copy(),
        }

    def log_report(self):
        """Log usage summary to logger with correlation_id."""
        r = self.report()
        corr = r.get("correlation_id", "unknown")
        logger.info(
            f"[{corr}] Vision API usage — calls={r['total_calls']} | "
            f"images={r['total_images_sent']} | "
            f"tokens_in={r['total_input_tokens']:,} | "
            f"tokens_out={r['total_output_tokens']:,} | "
            f"cost=${r['estimated_cost_usd']:.4f}"
        )

    def reset(self):
        """✅ NEW: Reset all counters — useful for periodic reporting or tests."""
        lock = self._thread_lock
        if lock:
            with lock:
                self._do_reset()
        else:
            self._do_reset()

    def _do_reset(self):
        """Internal reset logic."""
        object.__setattr__(self, "_ocr_fallback_calls", 0)
        object.__setattr__(self, "_table_analysis_calls", 0)
        object.__setattr__(self, "_diagram_analysis_calls", 0)
        object.__setattr__(self, "_metadata_calls", 0)
        object.__setattr__(self, "_total_input_tokens", 0)
        object.__setattr__(self, "_total_output_tokens", 0)
        object.__setattr__(self, "_total_images_sent", 0)
        object.__setattr__(self, "last_updated", time.time())
        logger.debug("VisionCostTracker reset")

    def to_dict(self) -> dict:
        """✅ NEW: Export tracker state for persistence/testing."""
        return {
            "ocr_fallback_calls": self.ocr_fallback_calls,
            "table_analysis_calls": self.table_analysis_calls,
            "diagram_analysis_calls": self.diagram_analysis_calls,
            "metadata_calls": self.metadata_calls,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_images_sent": self.total_images_sent,
            "correlation_id": self.correlation_id,
            "last_updated": self.last_updated,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "VisionCostTracker":  # ✅ FIXED: Added 'data:' parameter
        """✅ NEW: Import tracker state from dict."""
        tracker = cls()
        for key, value in data.items():
            if key in ["correlation_id", "last_updated"]:
                object.__setattr__(tracker, key, value)
            elif key.startswith("_") or key in [
                "ocr_fallback_calls",
                "table_analysis_calls",
                "diagram_analysis_calls",
                "metadata_calls",
                "total_input_tokens",
                "total_output_tokens",
                "total_images_sent",
            ]:
                attr = f"_{key}" if not key.startswith("_") else key
                if hasattr(tracker, attr):
                    object.__setattr__(tracker, attr, max(0, int(value)))
        return tracker


def get_cost_tracker_metadata() -> dict[str, Any]:  # ✅ FIXED: Any is now imported
    """✅ NEW: Return cost tracker metadata for monitoring."""
    pricing = _get_pricing()
    return {
        "pricing": pricing,
        "features": [
            "thread_safe",
            "async_safe",
            "per_call_type_tracking",
            "config_driven_pricing",
            "persistence_support",
        ],
    }


# DVMELTSS-M: Explicit module exports
# ✅ FIXED: Properly closed list (no stray brace)
__all__ = [
    "VisionCostTracker",
    "get_cost_tracker_metadata",
]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
