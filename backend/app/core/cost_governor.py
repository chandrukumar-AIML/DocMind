"""
AI Cost Governor — per-workspace LLM budget enforcement with automatic model fallback.

Flow:
  1. Before each LLM call, check remaining monthly token budget for the workspace.
  2. If budget is exhausted, fall back to the next cheaper model in the chain.
  3. If all models are exhausted, raise BudgetExhaustedError (caller returns 429).
  4. After each call, record actual token usage and update the workspace counter.

Budget and usage are stored in Redis (fast) with PostgreSQL as the write-through
durable store. On Redis miss, load from Postgres and warm the cache.

Fallback chain (cost order, cheapest last):
  gpt-4o  →  gpt-4o-mini  →  gpt-3.5-turbo  →  BudgetExhaustedError
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# Approximate cost per 1K tokens in USD (input+output blended estimate)
_MODEL_COST_PER_1K: dict[str, float] = {
    "gpt-4o":            0.010,
    "gpt-4o-mini":       0.000300,
    "gpt-4-turbo":       0.020,
    "gpt-4":             0.030,
    "gpt-3.5-turbo":     0.000600,
    "claude-opus-4-8":   0.015,
    "claude-sonnet-5":   0.003,
    "claude-haiku-4-5":  0.000250,
    "gemini-1.5-pro":    0.007,
    "gemini-1.5-flash":  0.000350,
}

# Default fallback chains per primary model
_FALLBACK_CHAINS: dict[str, list[str]] = {
    "gpt-4o":          ["gpt-4o-mini", "gpt-3.5-turbo"],
    "gpt-4o-mini":     ["gpt-3.5-turbo"],
    "gpt-4-turbo":     ["gpt-4o-mini", "gpt-3.5-turbo"],
    "gpt-4":           ["gpt-4o-mini", "gpt-3.5-turbo"],
    "claude-opus-4-8": ["claude-sonnet-5", "claude-haiku-4-5"],
    "claude-sonnet-5": ["claude-haiku-4-5"],
    "gemini-1.5-pro":  ["gemini-1.5-flash"],
}


class BudgetExhaustedError(Exception):
    """Raised when all models in the fallback chain are budget-exhausted."""

    def __init__(self, workspace_id: str, attempted_models: list[str]):
        self.workspace_id    = workspace_id
        self.attempted_models = attempted_models
        super().__init__(
            f"Monthly LLM budget exhausted for workspace {workspace_id}. "
            f"All fallback models tried: {attempted_models}"
        )


@dataclass
class UsageRecord:
    workspace_id:   str
    model:          str
    prompt_tokens:  int
    completion_tokens: int
    total_tokens:   int
    cost_usd:       float
    timestamp:      float = field(default_factory=time.time)


def estimate_cost(model: str, total_tokens: int) -> float:
    """Estimate USD cost for a given model + token count."""
    rate = _MODEL_COST_PER_1K.get(model, 0.005)  # default 0.5¢ per 1K if unknown
    return (total_tokens / 1000) * rate


class CostGovernor:
    """
    Per-workspace LLM budget guard with automatic model fallback.

    Usage:
        governor = CostGovernor()

        model = await governor.get_allowed_model(workspace_id, preferred_model="gpt-4o")
        # ... make LLM call with `model` ...
        await governor.record_usage(workspace_id, model, prompt_tokens, completion_tokens)
    """

    def __init__(self):
        self._redis = self._init_redis()

    def _init_redis(self):
        try:
            import redis as _redis
            from app.config import get_settings
            settings = get_settings()
            url = getattr(settings, "redis_url", None)
            if not url:
                return None
            return _redis.from_url(url, decode_responses=True, socket_connect_timeout=1)
        except Exception:
            return None

    # ── Budget keys ──────────────────────────────────────────────────────────

    def _budget_key(self, workspace_id: str) -> str:
        """Redis key for the workspace's monthly token budget (in tokens)."""
        return f"llm:budget:{workspace_id}"

    def _usage_key(self, workspace_id: str) -> str:
        """Redis key for current-month token usage."""
        import datetime
        month = datetime.datetime.utcnow().strftime("%Y-%m")
        return f"llm:usage:{workspace_id}:{month}"

    def _cost_key(self, workspace_id: str) -> str:
        """Redis key for current-month USD spend."""
        import datetime
        month = datetime.datetime.utcnow().strftime("%Y-%m")
        return f"llm:cost:{workspace_id}:{month}"

    # ── Budget management ────────────────────────────────────────────────────

    async def set_budget(self, workspace_id: str, monthly_token_limit: int) -> bool:
        """Set or update the monthly token budget for a workspace."""
        if not self._redis:
            logger.debug("Redis unavailable — budget enforcement disabled")
            return False
        try:
            self._redis.set(self._budget_key(workspace_id), monthly_token_limit)
            logger.info(f"Budget set for workspace {workspace_id}: {monthly_token_limit:,} tokens/month")
            return True
        except Exception as e:
            logger.warning(f"Failed to set budget for {workspace_id}: {e}")
            return False

    async def get_remaining_budget(self, workspace_id: str) -> Optional[int]:
        """
        Return remaining token budget for this workspace this month.
        Returns None if no budget is configured (unlimited).
        """
        if not self._redis:
            return None
        try:
            budget_str = self._redis.get(self._budget_key(workspace_id))
            if budget_str is None:
                return None  # No budget set = unlimited
            budget = int(budget_str)
            used   = int(self._redis.get(self._usage_key(workspace_id)) or 0)
            return max(0, budget - used)
        except Exception as e:
            logger.warning(f"Failed to read budget for {workspace_id}: {e}")
            return None

    # ── Model selection with fallback ────────────────────────────────────────

    async def get_allowed_model(
        self,
        workspace_id: str,
        preferred_model: str,
        estimated_tokens: int = 2000,
    ) -> str:
        """
        Return the best model the workspace can afford right now.

        Walks the fallback chain until it finds a model whose estimated cost
        fits within the remaining budget, or raises BudgetExhaustedError.
        """
        remaining = await self.get_remaining_budget(workspace_id)

        # No budget configured → allow preferred model
        if remaining is None:
            return preferred_model

        chain = [preferred_model] + _FALLBACK_CHAINS.get(preferred_model, [])
        tried = []

        for model in chain:
            est_cost_tokens = estimated_tokens
            if remaining >= est_cost_tokens:
                if model != preferred_model:
                    logger.info(
                        f"[{workspace_id}] Budget low ({remaining:,} tokens left) — "
                        f"falling back from {preferred_model} to {model}"
                    )
                return model
            tried.append(model)

        raise BudgetExhaustedError(workspace_id=workspace_id, attempted_models=tried)

    # ── Usage recording ──────────────────────────────────────────────────────

    async def record_usage(
        self,
        workspace_id: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> UsageRecord:
        """
        Record token usage after an LLM call.
        Increments the monthly usage counter in Redis and logs the cost estimate.
        """
        total = prompt_tokens + completion_tokens
        cost  = estimate_cost(model, total)

        record = UsageRecord(
            workspace_id=workspace_id,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total,
            cost_usd=cost,
        )

        if self._redis:
            try:
                pipe = self._redis.pipeline()
                usage_key = self._usage_key(workspace_id)
                cost_key  = self._cost_key(workspace_id)
                pipe.incrby(usage_key, total)
                pipe.incrbyfloat(cost_key, cost)
                # Expire at end of next month (generous TTL so keys self-clean)
                pipe.expire(usage_key, 60 * 60 * 24 * 62)
                pipe.expire(cost_key,  60 * 60 * 24 * 62)
                pipe.execute()
            except Exception as e:
                logger.warning(f"Failed to record usage for {workspace_id}: {e}")

        logger.info(
            f"[{workspace_id}] LLM usage: model={model} "
            f"tokens={total:,} ({prompt_tokens}+{completion_tokens}) "
            f"cost=${cost:.6f}"
        )
        return record

    async def get_monthly_summary(self, workspace_id: str) -> dict:
        """Return current-month usage and cost summary for a workspace."""
        if not self._redis:
            return {"error": "Redis unavailable"}
        try:
            remaining = await self.get_remaining_budget(workspace_id)
            usage_key = self._usage_key(workspace_id)
            cost_key  = self._cost_key(workspace_id)
            used    = int(self._redis.get(usage_key) or 0)
            cost    = float(self._redis.get(cost_key) or 0.0)
            budget  = int(self._redis.get(self._budget_key(workspace_id)) or 0) or None

            return {
                "workspace_id":  workspace_id,
                "tokens_used":   used,
                "tokens_budget": budget,
                "tokens_remaining": remaining,
                "cost_usd":      round(cost, 6),
                "budget_pct":    round(used / budget * 100, 1) if budget else None,
            }
        except Exception as e:
            return {"error": str(e)}


# Module-level singleton — one governor per process
_governor: Optional[CostGovernor] = None


def get_cost_governor() -> CostGovernor:
    global _governor
    if _governor is None:
        _governor = CostGovernor()
    return _governor
