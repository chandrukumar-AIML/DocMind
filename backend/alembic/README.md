# Database Schema Management

DocuMind AI uses a **two-layer schema strategy**:

## 1. Runtime bootstrap (current, active)
On application startup (`app/main.py` lifespan), the app runs:
- `Base.metadata.create_all()` — creates any missing tables (idempotent, never drops)
- `ensure_*_schema()` helpers — additive column/index repairs for evolving tables

This makes the app self-provisioning on a fresh database (verified on Supabase).
It is safe to run on every boot and requires no manual migration step.

## 2. Alembic (wired, ready for versioned migrations)
For teams that prefer explicit, reviewable migrations, Alembic is configured:
- `alembic/env.py` already targets `app.database.base.metadata`
- `DATABASE_URL` is read from settings/env

### Cut the baseline migration (run against a staging DB):
```bash
cd backend
export DATABASE_URL="postgresql+asyncpg://...staging..."   # never prod
alembic revision --autogenerate -m "baseline schema"
alembic upgrade head
```

### Day-to-day:
```bash
alembic revision --autogenerate -m "add X column"
alembic upgrade head      # apply
alembic downgrade -1      # rollback one step
```

> **Why both?** Runtime bootstrap keeps demos/dev frictionless; Alembic gives
> production teams auditable, reversible schema history. Generate the baseline
> from a staging DB (not production) so autogenerate diffs against a known state.
