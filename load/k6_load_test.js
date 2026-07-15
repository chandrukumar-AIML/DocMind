/**
 * DocMind AI — k6 Load & Performance Test Suite
 *
 * Scenarios:
 *   auth      — login + token refresh (warm baseline)
 *   ingest    — document upload + status polling
 *   query     — RAG query (the core hot path)
 *   spike     — 10× normal concurrency for 30 s (resilience check)
 *
 * Usage:
 *   BASE_URL=http://localhost:8000 k6 run load/k6_load_test.js
 *   BASE_URL=https://api.docmind.ai k6 run --env ENV=production load/k6_load_test.js
 *
 * Thresholds (fail CI if exceeded):
 *   http_req_duration p(95) < 2 000 ms  (query)
 *   http_req_duration p(99) < 5 000 ms  (ingest)
 *   http_req_failed  rate   < 0.01      (< 1 % errors)
 */

import http from "k6/http";
import { check, sleep, group } from "k6";
import { Trend, Rate, Counter } from "k6/metrics";

// ── Config ────────────────────────────────────────────────────────────────────

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const TEST_EMAIL = __ENV.TEST_EMAIL || "loadtest@docmind-ai.local";
const TEST_PASSWORD = __ENV.TEST_PASSWORD || "LoadTest@2025!";
const WORKSPACE_ID = __ENV.WORKSPACE_ID || "default";

// ── Custom metrics ────────────────────────────────────────────────────────────

const queryLatency = new Trend("rag_query_duration_ms", true);
const ingestLatency = new Trend("ingest_duration_ms", true);
const authLatency = new Trend("auth_duration_ms", true);
const errorRate = new Rate("errors");
const queryRequests = new Counter("rag_queries_total");

// ── Thresholds (CI gate) ──────────────────────────────────────────────────────

export const options = {
  scenarios: {
    // 1. Auth warm-up — 10 VUs × 2 min
    auth: {
      executor: "constant-vus",
      vus: 10,
      duration: "2m",
      exec: "authScenario",
      startTime: "0s",
      tags: { scenario: "auth" },
    },

    // 2. Normal query load — 30 VUs × 5 min (starts after auth warm-up)
    query: {
      executor: "constant-vus",
      vus: 30,
      duration: "5m",
      exec: "queryScenario",
      startTime: "2m",
      tags: { scenario: "query" },
    },

    // 3. Ingest — 5 VUs × 3 min (parallel with query)
    ingest: {
      executor: "constant-vus",
      vus: 5,
      duration: "3m",
      exec: "ingestScenario",
      startTime: "2m",
      tags: { scenario: "ingest" },
    },

    // 4. Spike — 300 VUs × 30 s at peak, then ramp down
    spike: {
      executor: "ramping-vus",
      startTime: "7m30s",
      stages: [
        { duration: "10s", target: 300 },
        { duration: "30s", target: 300 },
        { duration: "20s", target: 10 },
      ],
      exec: "queryScenario",
      tags: { scenario: "spike" },
    },
  },

  thresholds: {
    // Overall request latency (P95 < 2 s, P99 < 5 s)
    http_req_duration: ["p(95)<2000", "p(99)<5000"],

    // RAG query latency budget
    rag_query_duration_ms: ["p(95)<3000", "p(99)<8000"],

    // Error rate < 1 %
    errors: ["rate<0.01"],
    http_req_failed: ["rate<0.01"],
  },
};

// ── Shared helpers ────────────────────────────────────────────────────────────

function login() {
  const start = Date.now();
  const res = http.post(
    `${BASE_URL}/api/v1/auth/login`,
    JSON.stringify({ email: TEST_EMAIL, password: TEST_PASSWORD }),
    { headers: { "Content-Type": "application/json" } }
  );
  authLatency.add(Date.now() - start);

  const ok = check(res, {
    "login 200": (r) => r.status === 200,
    "login has access_token": (r) => {
      try {
        return JSON.parse(r.body).access_token !== undefined;
      } catch {
        return false;
      }
    },
  });
  errorRate.add(!ok);
  if (!ok) return null;

  return JSON.parse(res.body).access_token;
}

function authHeaders(token) {
  return {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
  };
}

// ── Scenario: Auth ────────────────────────────────────────────────────────────

export function authScenario() {
  group("auth", () => {
    const token = login();
    if (!token) {
      sleep(1);
      return;
    }

    // Token refresh
    const refreshRes = http.post(
      `${BASE_URL}/api/v1/auth/refresh`,
      "{}",
      { headers: authHeaders(token) }
    );
    check(refreshRes, { "refresh 200": (r) => r.status === 200 });
  });
  sleep(1);
}

// ── Scenario: RAG Query ───────────────────────────────────────────────────────

const SAMPLE_QUERIES = [
  "What are the key financial risks identified in Q3?",
  "Summarize the executive summary of the annual report",
  "What compliance requirements apply to data retention?",
  "List all parties mentioned in the contract",
  "What are the payment terms and conditions?",
];

export function queryScenario() {
  const token = login();
  if (!token) {
    sleep(2);
    return;
  }

  group("rag_query", () => {
    const query = SAMPLE_QUERIES[Math.floor(Math.random() * SAMPLE_QUERIES.length)];
    const start = Date.now();

    const res = http.post(
      `${BASE_URL}/api/v1/query`,
      JSON.stringify({
        question: query,
        workspace_id: WORKSPACE_ID,
        top_k_retrieve: 10,
        top_k_rerank: 3,
      }),
      { headers: authHeaders(token), timeout: "30s" }
    );

    const elapsed = Date.now() - start;
    queryLatency.add(elapsed);
    queryRequests.add(1);

    const ok = check(res, {
      "query 200": (r) => r.status === 200,
      "query has answer": (r) => {
        try {
          return JSON.parse(r.body).answer !== undefined;
        } catch {
          return false;
        }
      },
      "query < 15s": () => elapsed < 15000,
    });
    errorRate.add(!ok);
  });

  sleep(Math.random() * 2 + 1); // 1–3 s think time
}

// ── Scenario: Ingest ──────────────────────────────────────────────────────────

const SAMPLE_PDF_B64 =
  // Minimal 1-page PDF (base64) — replace with a real test fixture for accuracy
  "JVBERi0xLjQKMSAwIG9iago8PAovVHlwZSAvQ2F0YWxvZwovUGFnZXMgMiAwIFIKPj4KZW5kb2JqCjIgMCBvYmoKPDwKL1R5cGUgL1BhZ2VzCi9LaWRzIFszIDAgUl0KL0NvdW50IDEKPD4KZW5kb2JqCjMgMCBvYmoKPDwKL1R5cGUgL1BhZ2UKL1BhcmVudCAyIDAgUgovTWVkaWFCb3ggWzAgMCA2MTIgNzkyXQo+PgplbmRvYmoKeHJlZgowIDQKMDAwMDAwMDAwMCA2NTUzNSBmIAowMDAwMDAwMDA5IDAwMDAwIG4gCjAwMDAwMDAwNTggMDAwMDAgbiAKMDAwMDAwMDExNSAwMDAwMCBuIAp0cmFpbGVyCjw8Ci9TaXplIDQKL1Jvb3QgMSAwIFIKPj4Kc3RhcnR4cmVmCjE5MAolJUVPRg==";

export function ingestScenario() {
  const token = login();
  if (!token) {
    sleep(3);
    return;
  }

  group("ingest", () => {
    const pdfBytes = http.file(
      // k6 cannot decode base64 inline — in CI, provide a real test PDF via LOAD_TEST_PDF env
      new Uint8Array(Buffer.from(SAMPLE_PDF_B64, "base64")),
      "load_test.pdf",
      "application/pdf"
    );

    const formData = {
      file: pdfBytes,
      workspace_id: WORKSPACE_ID,
    };

    const start = Date.now();
    const res = http.post(`${BASE_URL}/api/v1/ingest/upload`, formData, {
      headers: { Authorization: `Bearer ${token}` },
      timeout: "60s",
    });
    ingestLatency.add(Date.now() - start);

    const ok = check(res, {
      "ingest accepted": (r) => r.status === 200 || r.status === 202,
    });
    errorRate.add(!ok);

    if (ok) {
      // Poll until processing completes (max 30 s)
      let taskId;
      try {
        taskId = JSON.parse(res.body).task_id;
      } catch {
        return;
      }

      if (taskId) {
        for (let i = 0; i < 6; i++) {
          sleep(5);
          const statusRes = http.get(
            `${BASE_URL}/api/v1/tasks/${taskId}`,
            { headers: authHeaders(token) }
          );
          const done = check(statusRes, {
            "task complete": (r) => {
              try {
                const body = JSON.parse(r.body);
                return body.status === "success" || body.status === "completed";
              } catch {
                return false;
              }
            },
          });
          if (done) break;
        }
      }
    }
  });

  sleep(5); // Ingest is expensive — longer think time
}

// ── Default export (k6 main) — runs query scenario unless overridden ──────────

export default function () {
  queryScenario();
}
