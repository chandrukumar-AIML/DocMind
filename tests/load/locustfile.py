"""
DocuMind AI Load Test — Locust
Simulates 50 concurrent users with realistic request patterns.

Run:
    pip install locust
    locust -f tests/load/locustfile.py \
           --host=http://localhost:8000 \
           --users=50 \
           --spawn-rate=5 \
           --run-time=5m \
           --headless \
           --html=load_test_report.html
"""
import json
import random
from locust import HttpUser, task, between, events
from locust.runners import MasterRunner


# Test data
SAMPLE_QUESTIONS = [
    "What are the payment terms?",
    "What is the liability cap in this contract?",
    "When does this agreement terminate?",
    "Who are the parties involved?",
    "What is the governing law?",
    "What are the confidentiality requirements?",
    "What notice period is required?",
    "What are the deliverables?",
    "What happens in case of breach?",
    "What is the total invoice amount?",
    "What is the invoice due date?",
    "Who is the vendor?",
    "What are the ICD-10 codes in this record?",
    "What medications were prescribed?",
    "Summarize the key findings of this document.",
]

# Pre-created test user credentials
TEST_EMAIL    = "loadtest@documind.local"
TEST_PASSWORD = "loadtest_password_123"


class DocuMindUser(HttpUser):
    """
    Simulates a typical DocuMind AI user.

    Task distribution:
    - 70% query existing documents
    - 20% list/browse documents
    - 10% health check / stats
    """
    wait_time = between(1, 4)    # 1-4s between requests
    token:      str = ""
    workspace_id: str = "default"

    def on_start(self):
        """Login and get token on user start."""
        # Try to login
        with self.client.post(
            "/api/v1/auth/login",
            data={
                "username": TEST_EMAIL,
                "password": TEST_PASSWORD,
            },
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                data             = resp.json()
                self.token       = data.get("access_token", "")
                self.workspace_id = data.get("workspace_id", "default")
            else:
                # Auth disabled — proceed without token
                self.token = ""

    def _headers(self) -> dict:
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}

    @task(7)
    def query_document(self):
        """Query documents — highest frequency task."""
        question = random.choice(SAMPLE_QUESTIONS)
        with self.client.post(
            "/api/v1/query",
            json={
                "question":      question,
                "stream":        False,
                "top_k_retrieve": 10,
                "top_k_rerank":  3,
            },
            headers=self._headers(),
            catch_response=True,
            name="/api/v1/query",
        ) as resp:
            if resp.status_code == 200:
                data = resp.json()
                if not data.get("answer"):
                    resp.failure("Empty answer returned")
                else:
                    resp.success()
            elif resp.status_code == 429:
                resp.failure("Rate limited")
            elif resp.status_code == 503:
                resp.failure("Service unavailable")
            else:
                resp.failure(f"HTTP {resp.status_code}")

    @task(2)
    def list_documents(self):
        """List indexed documents."""
        with self.client.get(
            "/api/v1/documents",
            headers=self._headers(),
            catch_response=True,
            name="/api/v1/documents",
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"HTTP {resp.status_code}")

    @task(1)
    def health_check(self):
        """Health check — low frequency."""
        with self.client.get(
            "/health",
            catch_response=True,
            name="/health",
        ) as resp:
            if resp.status_code == 200:
                data   = resp.json()
                status = data.get("status", "")
                if status in ("ok", "degraded"):
                    resp.success()
                else:
                    resp.failure(f"Unhealthy status: {status}")
            else:
                resp.failure(f"HTTP {resp.status_code}")


class HeavyUser(DocuMindUser):
    """
    Simulates power users with heavier workloads.
    10% of users are heavy — they query more frequently.
    """
    wait_time = between(0.5, 2)

    @task(5)
    def query_agent(self):
        """Use the full agent pipeline."""
        question = random.choice(SAMPLE_QUESTIONS)
        with self.client.post(
            "/api/v1/agent-query",
            json={
                "question":   question,
                "stream":     False,
            },
            headers=self._headers(),
            catch_response=True,
            name="/api/v1/agent-query",
        ) as resp:
            if resp.status_code in (200, 202):
                resp.success()
            elif resp.status_code == 429:
                resp.failure("Rate limited")
            else:
                resp.failure(f"HTTP {resp.status_code}")

    @task(3)
    def get_monitoring_stats(self):
        """Check monitoring dashboard."""
        with self.client.get(
            "/api/v1/monitoring/stats?hours=1",
            headers=self._headers(),
            catch_response=True,
            name="/api/v1/monitoring/stats",
        ) as resp:
            if resp.status_code in (200, 403):  # 403 if not admin
                resp.success()
            else:
                resp.failure(f"HTTP {resp.status_code}")


# ── Performance targets ────────────────────────────────────────────────────────
PERFORMANCE_TARGETS = {
    "/api/v1/query":       {"p95_ms": 8000,  "error_rate": 0.01},
    "/api/v1/agent-query": {"p95_ms": 15000, "error_rate": 0.02},
    "/api/v1/documents":   {"p95_ms": 500,   "error_rate": 0.005},
    "/health":             {"p95_ms": 100,   "error_rate": 0.0},
}


@events.quitting.add_listener
def on_quitting(environment, **kwargs):
    """Print performance summary on test completion."""
    if not isinstance(environment.runner, MasterRunner):
        return

    print("\n" + "=" * 60)
    print("LOAD TEST RESULTS SUMMARY")
    print("=" * 60)

    all_passed = True
    for name, target in PERFORMANCE_TARGETS.items():
        stats = environment.runner.stats.get(name, "GET")
        if not stats:
            continue

        p95        = stats.get_response_time_percentile(0.95)
        error_rate = stats.fail_ratio

        p95_ok    = p95 <= target["p95_ms"]
        error_ok  = error_rate <= target["error_rate"]
        passed    = p95_ok and error_ok

        if not passed:
            all_passed = False

        status = "✅ PASS" if passed else "❌ FAIL"
        print(
            f"\n{status} {name}\n"
            f"  P95: {p95:.0f}ms (target: <{target['p95_ms']}ms) "
            f"{'✓' if p95_ok else '✗'}\n"
            f"  Error rate: {error_rate:.2%} "
            f"(target: <{target['error_rate']:.2%}) "
            f"{'✓' if error_ok else '✗'}"
        )

    print("\n" + "=" * 60)
    if all_passed:
        print("ALL PERFORMANCE TARGETS MET ✅")
    else:
        print("SOME TARGETS MISSED — review before deployment ❌")
        environment.process_exit_code = 1
    print("=" * 60)