# Security Audit — DocuMind AI

**Date:** 2026-07-03  **Scope:** backend (FastAPI) + frontend (React) + dependency posture
**Method:** manual architecture review + `pip-audit` dependency scan + git-history secret scan

> This is an internal pre-sale readiness assessment, not a third-party penetration test.
> An independent pentest / SOC 2 audit is still recommended before signing enterprise
> customers who require it.

---

## 1. Security architecture — **Strong** ✅

Verified solid in the areas that constitute the actual attack surface:

| Area | Status | Notes |
|---|---|---|
| **Authentication** | ✅ | JWT via `python-jose`; 64-char secret **enforced** + weak/placeholder guard (`app/config.py`); bcrypt password hashing; httpOnly cookie + Bearer dual auth |
| **API-key auth** | ✅ | Hashed at rest (SHA-256), constant-time verify, workspace-scoped; middleware + `get_current_user` wired (session 9) |
| **Secrets** | ✅ | **None committed** — verified across full git history; `.gitignore` correct; `.env` files untracked; secrets encrypted at rest via Fernet (`app/core/crypto.py`) |
| **Tenant isolation** | ✅ | Per-workspace Chroma collections / FAISS indexes / BM25 caches (session 9); no cross-tenant data access |
| **Transport/headers** | ✅ | Prod: HSTS (preload), strict CSP, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff` (`app/security.py`) |
| **CORS** | ✅ | Explicit origins; validator rejects `["*"]` with credentials |
| **SQL injection** | ✅ | Parameterized `text()` bindings throughout; no user input interpolated into SQL |
| **Rate limiting** | ✅ | Redis-backed (auth, query, ingest), in-memory fallback |
| **Webhook integrity** | ✅ | Stripe signature verified (HMAC, constant-time), verified E2E (session 9b) |

**No secrets found in tracked files or git history.** The only `sk_*` strings in the repo
are documentation placeholders in `config.py` field descriptions and `.env.example`.

---

## 2. Dependency CVEs — the normal ML-stack backlog

`pip-audit` reported **148 findings across ~30 packages**. Triaged by real risk:

### Defer — framework majors (breaking upgrades)
Fix versions are **major releases** the agent/RAG pipeline is built on — cannot be bumped
without a dedicated, regression-tested upgrade sprint:

- `langchain` 0.3.29 → 1.3.9, `langgraph` 0.3.34 → 1.0, `langgraph-checkpoint` → 4.x
- `transformers` 4.36.0 → 5.x (28 findings), `mlflow` (40 findings)
- `starlette` 0.37.2 (8) — pinned by FastAPI; bump requires a FastAPI upgrade

### ~~Safe to bump~~ — **DONE (2026-07-03)** ✅
Bumped and verified against the full 145-test suite; security floors pinned in
`backend/requirements.txt`:
- `cryptography` 48.0.0 → 49.0.0 (Fernet at-rest encryption path)
- `pillow` 10.4 → 12.3 (image OCR input surface — PaddleOCR compatibility test-verified)
- `urllib3` → 2.7.0, `idna` → 3.18, `ujson` → 5.13.0, `python-multipart` → 0.0.32, `bleach` → 6.4.0

### Not in our code path
- `pyjwt` (8) — **transitive only**; our auth uses `python-jose`, so these do not affect
  the authentication path.

### Not auditable via PyPI
- `torch`/`torchaudio`/`torchvision` (CPU builds) — track via the PyTorch security channel.

**Reproduce:** `cd backend && .venv/Scripts/python.exe -m pip_audit --progress-spinner off`
(On Windows, add `PYTHONUTF8=1` when using `-r requirements.txt` — the file's UTF-8
box-drawing comment characters trip the cp1252 default locale.)

---

## 3. Recommendation

1. **Ship as-is for pilot/design-partner customers** — the security *architecture* is
   sound; the CVE list is the standard backlog every Python-ML project carries and none
   of it is in the authentication path.
2. **Safe leaf-lib bumps** (§2) as a small, test-verified chore before GA.
3. **Framework-major upgrade** as a scoped sprint (full regression testing of OCR /
   reranker / agent pipeline) — do this when a customer's security review demands a
   near-zero-CVE SBOM, not before.
4. Commission an **independent pentest** before contractually committing to enterprise
   security guarantees.
