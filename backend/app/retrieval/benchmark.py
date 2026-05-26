# backend/app/retrieval/benchmark.py
# DVMELTSS-FIX: V - Validate, E - Error handling, A - Async
# BATMAN-FIX: A - True async, T - Concurrent execution + timeout guards
# ✅ FIXED: Sync search_fn wrapped in thread executor (no event loop block)
# ✅ FIXED: Per-query timeout + global concurrency limit
# ✅ FIXED: Safe kwargs filtering for search_fn signature compatibility
# ✅ FIXED: Memory guard + chunked processing for large benchmarks
# ✅ FIXED: Zero-division guard + deterministic percentile calculation

from __future__ import annotations
import asyncio
import inspect
import logging
import sys
import time
from dataclasses import dataclass, field
from typing import Final, Optional, Callable, Awaitable

from app.core.retrieval_utils import generate_retrieval_correlation_id, validate_top_k

logger = logging.getLogger(__name__)

# DVMELTSS-S: Benchmark configuration
_DEFAULT_QUERIES: Final = 50
_DEFAULT_TOP_K: Final = 10
_CONCURRENCY_LIMIT: Final = 5
_GLOBAL_CONCURRENCY_LIMIT: Final = 10  # ✅ NEW: Global cap across all methods
_QUERY_TIMEOUT_SECONDS: Final = 30.0  # ✅ NEW: Per-query timeout
_MAX_BENCHMARK_QUERIES: Final = 500  # ✅ NEW: Memory safety guard
_CHUNK_SIZE: Final = 50  # ✅ NEW: Process queries in chunks to free memory


@dataclass(frozen=True)
class BenchmarkResult:
    """Immutable benchmark metrics for a retrieval method."""
    method_name: str
    avg_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    success_rate: float
    total_queries: int
    successful_queries: int
    correlation_id: Optional[str] = None
    
    def to_dict(self) -> dict[str, any]:
        return {
            "method": self.method_name,
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "p95_latency_ms": round(self.p95_latency_ms, 2),
            "p99_latency_ms": round(self.p99_latency_ms, 2),
            "success_rate": round(self.success_rate, 2),
            "total_queries": self.total_queries,
            "successful_queries": self.successful_queries,
            "correlation_id": self.correlation_id,
        }


@dataclass
class RetrievalBenchmark:
    """
    Benchmark utility for comparing retrieval methods.
    
    Usage:
    benchmark = RetrievalBenchmark()
    results = await benchmark.run_comparison(
        queries=test_queries,
        methods={"dense": dense_search, "hybrid": hybrid_search},
        k=10
    )
    """
    
    def __init__(self, global_concurrency_limit: int = _GLOBAL_CONCURRENCY_LIMIT):
        # ✅ NEW: Global semaphore to prevent system overload when comparing methods
        self._global_semaphore = asyncio.Semaphore(global_concurrency_limit)
        logger.info(f"RetrievalBenchmark initialized: global_concurrency={global_concurrency_limit}")
    
    # ✅ NEW: Helper for Python 3.8 compatibility
    async def _run_in_thread(self, func: Callable, *args, **kwargs):
        """Run blocking function in thread pool — compatible with Python 3.8+."""
        if sys.version_info >= (3, 9):
            return await asyncio.to_thread(func, *args, **kwargs)
        else:
            loop = asyncio.get_running_loop()  # FIXED: get_event_loop() deprecated in Python 3.10+
            return await loop.run_in_executor(None, lambda: func(*args, **kwargs))
    
    # ✅ NEW: Safe kwargs filter — only pass params that search_fn accepts
    def _filter_kwargs(self, fn: Callable, **kwargs) -> dict[str, any]:
        """Filter kwargs to only include parameters accepted by fn."""
        try:
            sig = inspect.signature(fn)
            params = set(sig.parameters.keys())
            # Always allow *args, **kwargs functions to receive everything
            if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
                return kwargs
            return {k: v for k, v in kwargs.items() if k in params}
        except Exception:
            # On introspection failure, pass all kwargs (safe fallback)
            return kwargs
    
    async def run_single_method(
        self,
        method_name: str,
        search_fn: Callable,
        queries: list[dict[str, any]],
        k: int = _DEFAULT_TOP_K,
        correlation_id: Optional[str] = None,
        timeout_seconds: float = _QUERY_TIMEOUT_SECONDS,
    ) -> BenchmarkResult:
        """
        Benchmark a single retrieval method.
        ✅ FIXED: Thread-wrapped sync calls + per-query timeout + safe kwargs.
        """
        corr_id = correlation_id or generate_retrieval_correlation_id(f"bench_{method_name}")
        k = validate_top_k(k)
        
        # ✅ NEW: Memory guard — truncate if too many queries
        if len(queries) > _MAX_BENCHMARK_QUERIES:
            logger.warning(
                f"[{corr_id}] Query list too large ({len(queries)} > {_MAX_BENCHMARK_QUERIES}) — truncating"
            )
            queries = queries[:_MAX_BENCHMARK_QUERIES]
        
        latencies: list[float] = []
        successes = 0
        
        # ✅ NEW: Per-method semaphore + global semaphore for nested limiting
        method_semaphore = asyncio.Semaphore(_CONCURRENCY_LIMIT)
        
        async def run_query(query: dict[str, any]) -> Optional[float]:
            async with method_semaphore, self._global_semaphore:  # ✅ Double-limiting
                start = time.perf_counter()
                try:
                    # ✅ Filter kwargs to match search_fn signature
                    safe_kwargs = self._filter_kwargs(
                        search_fn,
                        query=query.get("text", ""),
                        query_embedding=query.get("embedding"),
                        k=k,
                        correlation_id=corr_id,
                    )
                    
                    # Handle both sync and async search functions
                    if asyncio.iscoroutinefunction(search_fn):
                        await asyncio.wait_for(
                            search_fn(**safe_kwargs),
                            timeout=timeout_seconds,
                        )
                    else:
                        # ✅ FIXED: Wrap sync call in thread to avoid blocking event loop
                        await asyncio.wait_for(
                            self._run_in_thread(search_fn, **safe_kwargs),
                            timeout=timeout_seconds,
                        )
                    
                    return time.perf_counter() - start
                    
                except asyncio.TimeoutError:
                    logger.warning(f"[{corr_id}] {method_name} query timed out after {timeout_seconds}s")
                    return None
                except Exception as e:
                    logger.warning(f"[{corr_id}] {method_name} query failed: {type(e).__name__}: {e}")
                    return None
        
        # ✅ NEW: Process in chunks to free memory between batches
        all_results = []
        for i in range(0, len(queries), _CHUNK_SIZE):
            chunk = queries[i:i + _CHUNK_SIZE]
            tasks = [run_query(q) for q in chunk]
            chunk_results = await asyncio.gather(*tasks, return_exceptions=True)
            all_results.extend(chunk_results)
            # Optional: small delay between chunks to let GC run
            await asyncio.sleep(0.01)
        
        # Compute metrics
        valid_latencies = [l for l in all_results if isinstance(l, (int, float)) and l >= 0]
        successes = len(valid_latencies)
        total = len(queries)
        
        # ✅ FIXED: Safe division guard
        if not valid_latencies or total == 0:
            return BenchmarkResult(
                method_name=method_name,
                avg_latency_ms=0.0,
                p95_latency_ms=0.0,
                p99_latency_ms=0.0,
                success_rate=0.0,
                total_queries=total,
                successful_queries=0,
                correlation_id=corr_id,
            )
        
        # Convert to milliseconds and sort for percentiles
        latencies_ms = sorted([l * 1000 for l in valid_latencies])
        
        # ✅ FIXED: Deterministic percentile calculation (nearest-rank method)
        def percentile(data: list[float], p: float) -> float:
            if not data:
                return 0.0
            idx = min(int(len(data) * p / 100), len(data) - 1)
            return data[idx]
        
        return BenchmarkResult(
            method_name=method_name,
            avg_latency_ms=sum(latencies_ms) / len(latencies_ms),
            p95_latency_ms=percentile(latencies_ms, 95),
            p99_latency_ms=percentile(latencies_ms, 99),
            success_rate=successes / total,
            total_queries=total,
            successful_queries=successes,
            correlation_id=corr_id,
        )
    
    async def run_comparison(
        self,
        queries: list[dict[str, any]],
        methods: dict[str, Callable],
        k: int = _DEFAULT_TOP_K,
        correlation_id: Optional[str] = None,
        timeout_seconds: float = _QUERY_TIMEOUT_SECONDS,
    ) -> dict[str, BenchmarkResult]:
        """
        Benchmark multiple retrieval methods for comparison.
        ✅ FIXED: Global concurrency limit prevents system overload.
        """
        corr_id = correlation_id or generate_retrieval_correlation_id("bench_compare")
        
        # ✅ Memory guard
        if len(queries) > _MAX_BENCHMARK_QUERIES:
            logger.warning(
                f"[{corr_id}] Query list too large ({len(queries)} > {_MAX_BENCHMARK_QUERIES}) — truncating"
            )
            queries = queries[:_MAX_BENCHMARK_QUERIES]
        
        logger.info(f"[{corr_id}] Starting benchmark comparison: {len(methods)} methods, {len(queries)} queries")
        
        # Run all methods concurrently (global semaphore limits total concurrency)
        tasks = [
            self.run_single_method(name, fn, queries, k, corr_id, timeout_seconds)
            for name, fn in methods.items()
        ]
        
        results = await asyncio.gather(*tasks)
        
        # Build result dict
        result_dict = {r.method_name: r for r in results}
        
        # Log summary
        logger.info(f"[{corr_id}] Benchmark complete:")
        for name, result in sorted(result_dict.items()):
            logger.info(
                f"  {name}: avg={result.avg_latency_ms:.1f}ms, "
                f"p95={result.p95_latency_ms:.1f}ms, "
                f"success={result.success_rate:.1%}"
            )
        
        return result_dict
    
    async def run_stress_test(
        self,
        search_fn: Callable,
        query_template: dict[str, any],
        num_queries: int = 100,
        k: int = _DEFAULT_TOP_K,
        correlation_id: Optional[str] = None,
    ) -> BenchmarkResult:
        """
        ✅ NEW: Generate synthetic queries for stress testing.
        
        Args:
            search_fn: Retrieval function to test
            query_template: Base query dict to clone/modify
            num_queries: Number of synthetic queries to generate
            k: Top-k parameter
            correlation_id: Request ID for tracing
        """
        import random
        import string
        
        corr_id = correlation_id or generate_retrieval_correlation_id("stress")
        
        # Generate synthetic queries by appending random suffixes
        queries = []
        for i in range(num_queries):
            suffix = ''.join(random.choices(string.ascii_lowercase, k=8))
            query = query_template.copy()
            if "text" in query:
                query["text"] = f"{query['text']} {suffix}"
            queries.append(query)
        
        logger.info(f"[{corr_id}] Running stress test: {num_queries} synthetic queries")
        return await self.run_single_method(
            method_name="stress_test",
            search_fn=search_fn,
            queries=queries,
            k=k,
            correlation_id=corr_id,
        )


# DVMELTSS-M: Explicit module exports
__all__ = ["RetrievalBenchmark", "BenchmarkResult"]
# Local smoke test entry point. Run: python -m 
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)

