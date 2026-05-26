# DocuMind AI — Free Deployment Guide

Deploy the full stack for **$0/month** using:

| Service | What | Free Tier Limit |
|---|---|---|
| **Vercel** | Frontend (React/Vite) | Unlimited static, 100GB bandwidth |
| **Render** | Backend (FastAPI) | 750 hrs/month, 512 MB RAM |
| **Supabase** | PostgreSQL 16 | 500 MB DB, 2 GB bandwidth |
| **Upstash** | Redis | 10,000 commands/day |

> **Note on Render free tier**: The instance sleeps after 15 min of inactivity and takes ~30 s to cold-start. Upgrade to Starter ($7/mo) for always-on. For portfolio demos, free is fine.

---

## Pre-Deploy Checklist

Run this before you start:

```bash
# 1. Make sure your latest code is committed
git status
git add -A
git commit -m "pre-deploy cleanup"

# 2. Generate a strong JWT secret (run in any terminal)
python -c "import secrets; print(secrets.token_hex(64))"
# Copy the output — you'll need it in step 4
```

---

## Step 1 — Supabase (PostgreSQL)

1. Go to [supabase.com](https://supabase.com) → **New Project**
2. Set a strong **Database Password** (save it)
3. Choose the region closest to you
4. Wait ~2 min for provisioning
5. Go to **Settings → Database** → copy the **Connection string (URI)**
   - It looks like: `postgresql://postgres:[PASSWORD]@db.[REF].supabase.co:5432/postgres`
6. Replace `postgresql://` with `postgresql+asyncpg://` (required for SQLAlchemy async)

> Save this — you'll use it as `DATABASE_URL` in Render.

---

## Step 2 — Upstash Redis

1. Go to [upstash.com](https://upstash.com) → **Create Database**
2. Name: `docmind-redis`, Region: nearest to you, Type: **Regional**
3. After creation, go to **Details** → copy the **Redis URL**
   - It looks like: `rediss://default:[PASSWORD]@[HOST].upstash.io:6379`

> Save this — you'll use it as `REDIS_URL` in Render.

---

## Step 3 — Render (Backend)

### 3a. Push your code to GitHub first

```bash
# If you haven't pushed yet:
git remote add origin https://github.com/YOUR_USERNAME/docmind-ai.git
git branch -M main
git push -u origin main
```

### 3b. Create the Web Service

1. Go to [render.com](https://render.com) → **New → Web Service**
2. Connect your GitHub repo → select `docmind-ai`
3. Set:
   - **Name**: `docmind-backend`
   - **Root Directory**: `backend`
   - **Runtime**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - **Plan**: Free

### 3c. Add Persistent Disk

1. In your service → **Disks** → **Add Disk**
2. Set:
   - **Name**: `docmind-data`
   - **Mount Path**: `/data`
   - **Size**: 1 GB (free)

> This is where ChromaDB and FAISS indexes will persist between deploys.

### 3d. Set Environment Variables

In Render → your service → **Environment** → add each one:

```
ENVIRONMENT=production
API_HOST=0.0.0.0
API_RELOAD=false

# Auth
AUTH_ENABLED=true
ALLOW_SELF_REGISTRATION=false
JWT_SECRET_KEY=<paste the 128-char secret from pre-deploy step>
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60
JWT_REFRESH_TOKEN_EXPIRE_DAYS=30
DEFAULT_WORKSPACE_ID=default

# CORS — fill in your Vercel URL (you'll know it after step 4)
CORS_ORIGINS=["https://YOUR-APP.vercel.app"]
FRONTEND_URL=https://YOUR-APP.vercel.app

# Database (from Supabase step 1)
DATABASE_URL=postgresql+asyncpg://postgres:[PASSWORD]@db.[REF].supabase.co:5432/postgres

# Redis (from Upstash step 2)
REDIS_URL=rediss://default:[PASSWORD]@[HOST].upstash.io:6379

# OpenAI — required for embeddings + LLM
OPENAI_API_KEY=sk-...
OPENAI_EMBEDDING_MODEL=text-embedding-3-large
OPENAI_CHAT_MODEL=gpt-4o
LLM_PROVIDER=openai

# Vector stores
CHROMA_PERSIST_DIR=/data/chroma
FAISS_INDEX_PATH=/data/faiss/index.bin
CHROMA_COLLECTION_NAME=documind_docs

# Misc
TMP_DIR=/tmp/documind
MAX_UPLOAD_SIZE_MB=50
```

> **Skip** Neo4j, MLflow, Celery — they're optional. The app falls back gracefully.

### 3e. Deploy

Click **Deploy**. Watch the logs — first deploy takes ~3–4 minutes (installing dependencies).

When you see `Application startup complete`, note your Render URL:
`https://docmind-backend.onrender.com`

### 3f. Initialize the Database

Once deployed, open the Render **Shell** tab and run:

```bash
python -c "
import asyncio
from app.database.session import init_db
asyncio.run(init_db())
print('DB initialized')
"
```

Then seed demo data (optional):

```bash
python seed_data.py
```

---

## Step 4 — Vercel (Frontend)

### 4a. Import project

1. Go to [vercel.com](https://vercel.com) → **Add New Project**
2. Import your GitHub repo
3. Set:
   - **Root Directory**: `frontend`
   - **Framework Preset**: Vite
   - **Build Command**: `npm run build`
   - **Output Directory**: `dist`

### 4b. Set Environment Variables

In Vercel → your project → **Settings → Environment Variables**:

```
VITE_API_URL=https://docmind-backend.onrender.com
VITE_WS_URL=wss://docmind-backend.onrender.com
VITE_APP_NAME=DocuMind AI
VITE_APP_VERSION=2.0.0
VITE_DEMO_MODE=false
```

### 4c. Deploy

Click **Deploy**. Vercel builds in ~60 seconds.

Your frontend URL: `https://docmind-ai.vercel.app` (or similar)

### 4d. Update CORS on Render

Go back to Render → Environment → update:

```
CORS_ORIGINS=["https://docmind-ai.vercel.app"]
FRONTEND_URL=https://docmind-ai.vercel.app
```

Click **Save** → Render will redeploy automatically.

---

## Step 5 — Verify Everything Works

```bash
# 1. Health check
curl https://docmind-backend.onrender.com/health

# 2. API docs
# Open: https://docmind-backend.onrender.com/docs

# 3. Frontend
# Open: https://docmind-ai.vercel.app
# Login with: admin@docmind.ai / AdminP@ssw0rd!2026
```

---

## Test Credentials (after seeding)

| Email | Password | Role |
|---|---|---|
| `admin@docmind.ai` | `AdminP@ssw0rd!2026` | Admin |
| `demo@docmind.ai` | `DemoP@ssw0rd!2026` | Editor |

---

## Custom Domain (Optional, Free)

**Vercel**: Settings → Domains → add your domain → update DNS CNAME to `cname.vercel-dns.com`

**Render**: Settings → Custom Domains → add your domain → update DNS

---

## Future Upgrades (When You Need Them)

| Need | Upgrade |
|---|---|
| Always-on backend (no cold start) | Render Starter $7/mo |
| More DB storage | Supabase Pro $25/mo |
| More Redis commands | Upstash Pay-as-you-go |
| Background jobs (Celery) | Add Render Worker service |
| Graph queries (Neo4j) | Neo4j AuraDB Free (1 instance) |

---

## Environment Variables Quick Reference

| Variable | Where to set | Value |
|---|---|---|
| `DATABASE_URL` | Render | Supabase connection string (asyncpg) |
| `REDIS_URL` | Render | Upstash Redis URL |
| `OPENAI_API_KEY` | Render | Your OpenAI key |
| `JWT_SECRET_KEY` | Render | 128-char random secret |
| `CORS_ORIGINS` | Render | `["https://your-app.vercel.app"]` |
| `FRONTEND_URL` | Render | `https://your-app.vercel.app` |
| `VITE_API_URL` | Vercel | `https://docmind-backend.onrender.com` |
| `CHROMA_PERSIST_DIR` | Render | `/data/chroma` |
| `FAISS_INDEX_PATH` | Render | `/data/faiss/index.bin` |
