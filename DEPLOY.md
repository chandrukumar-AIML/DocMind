# DocuMind AI — Production Deploy Guide

## Stack

| Service | What | Cost |
|---|---|---|
| **Railway** | Backend (FastAPI) + PostgreSQL + Redis | $5/mo Hobby |
| **Cloudflare Pages** | Frontend (React/Vite) | Free forever |
| **Razorpay** | Payments (INR) | Free account, 2% per transaction |

---

## Pre-Deploy — Run once on your machine

```bash
# 1. Make sure all code is committed
git add -A
git commit -m "production deploy"

# 2. Generate a strong JWT secret
python -c "import secrets; print(secrets.token_hex(64))"
# Copy the output — paste as JWT_SECRET_KEY in Railway
```

---

## Step 1 — Railway (Backend + DB + Redis)

### 1.1 Create Railway project

1. Go to [railway.app](https://railway.app) → Sign up / Login
2. **New Project → Deploy from GitHub repo**
3. Select your `docmind-ai` repo
4. Railway auto-detects the `backend/` folder's `Dockerfile`

### 1.2 Add PostgreSQL

In your Railway project → **+ New → Database → PostgreSQL**
- Railway auto-injects `DATABASE_URL` — nothing to copy manually

### 1.3 Add Redis

**+ New → Database → Redis**
- Railway auto-injects `REDIS_URL` — nothing to copy manually

### 1.4 Set environment variables

In your backend service → **Variables** tab → paste each of these:

```env
# ── App ──────────────────────────────────────────────────────
APP_NAME=DocuMind AI
ENVIRONMENT=production
API_RELOAD=false
AUTH_ENABLED=true
SKIP_EMAIL_VERIFICATION=false

# ── JWT (paste output from python command above) ─────────────
JWT_SECRET_KEY=PASTE_YOUR_64_CHAR_SECRET_HERE

# ── CORS (update after Cloudflare Pages deploy) ──────────────
CORS_ORIGINS=["https://your-app.pages.dev","https://yourdomain.com"]

# ── Frontend URL (update after Cloudflare deploy) ────────────
FRONTEND_URL=https://your-app.pages.dev

# ── Encryption key (generate once) ──────────────────────────
# python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
ENCRYPTION_KEY=PASTE_GENERATED_FERNET_KEY

# ── LLM (Groq free tier — get key at console.groq.com) ──────
GROQ_API_KEY=gsk_your_groq_key
GROQ_MODEL=llama-3.3-70b-versatile
LLM_PROVIDER=groq

# ── Embeddings (Voyage AI free — dash.voyageai.com) ─────────
VOYAGE_API_KEY=pa-your_voyage_key
VOYAGE_MODEL=voyage-3-lite
EMBEDDING_PROVIDER=voyage

# ── OCR (Mistral free — console.mistral.ai) ─────────────────
MISTRAL_API_KEY=your_mistral_key

# ── Razorpay ─────────────────────────────────────────────────
RAZORPAY_KEY_ID=rzp_live_your_key_id
RAZORPAY_KEY_SECRET=your_razorpay_secret
RAZORPAY_WEBHOOK_SECRET=your_webhook_secret
RAZORPAY_PLAN_ID_STARTER=plan_your_starter_id
RAZORPAY_PLAN_ID_PRO=plan_your_pro_id

# ── OCR config ───────────────────────────────────────────────
OCR_USE_GPU=false
OCR_LANGUAGES=en,hi,ta

# ── Storage paths (Railway persistent volume) ────────────────
CHROMA_PERSIST_DIR=/data/chroma
FAISS_INDEX_PATH=/data/faiss/index.bin
TMP_DIR=/tmp/documind
```

> **Tip:** Railway auto-provides `DATABASE_URL` and `REDIS_URL` from the databases you added — don't set these manually, Railway fills them in automatically.

### 1.5 Add persistent volume

Backend service → **Settings → Volumes → Add Volume**
- Mount path: `/data`
- This stores Chroma + FAISS index between deploys

### 1.6 Deploy

Click **Deploy** — Railway builds the Docker image and starts the service.
- First build takes ~5–8 min (downloads Python deps)
- After that: ~2 min per deploy (cached layers)

Check logs: your backend URL will be something like `https://documind-backend.up.railway.app`

Test it: `https://your-backend.up.railway.app/health` should return `{"status":"ok"}`

### 1.7 Set Razorpay webhook URL

Railway dashboard → your backend service → copy the public URL

Razorpay dashboard → **Settings → Webhooks → Edit your webhook**
- URL: `https://your-backend.up.railway.app/api/v1/razorpay/webhook`

---

## Step 2 — Cloudflare Pages (Frontend)

### 2.1 Connect repo

1. Go to [pages.cloudflare.com](https://pages.cloudflare.com) → **Create a project**
2. **Connect to Git → select your repo**
3. Configure build:

| Setting | Value |
|---|---|
| Framework preset | Vite |
| Root directory | `frontend` |
| Build command | `npm run build` |
| Output directory | `dist` |

### 2.2 Set environment variables

In Cloudflare Pages → **Settings → Environment variables**:

```env
VITE_API_URL=https://your-backend.up.railway.app
VITE_DEMO_MODE=false
```

### 2.3 Deploy

Click **Save and Deploy** — Cloudflare builds and publishes.
Your app is live at: `https://your-project.pages.dev`

### 2.4 Update Railway CORS

Go back to Railway → update these two variables with your Cloudflare URL:

```env
CORS_ORIGINS=["https://your-project.pages.dev"]
FRONTEND_URL=https://your-project.pages.dev
```

---

## Step 3 — Run DB migration (once after first deploy)

After your Railway backend is live, run the migration to fix plan defaults:

```bash
# In Railway dashboard → your backend service → Shell tab → run:
python migrate_plan_defaults.py
```

---

## Step 4 — Custom domain (optional)

**Cloudflare Pages:** Settings → Custom Domains → Add `app.yourdomain.com`

**Railway:** Settings → Networking → Custom Domain → Add `api.yourdomain.com`

Update `CORS_ORIGINS` and `FRONTEND_URL` in Railway to match.

---

## Verify everything works

```
✅ https://your-app.pages.dev          → Landing page loads
✅ https://your-app.pages.dev/app      → Login page loads
✅ https://your-backend.railway.app/health  → {"status":"ok"}
✅ Register a new account → lands on free plan (5 docs, 50 queries)
✅ Features → Billing → plan cards show ₹ prices
✅ Upgrade button → Razorpay modal opens with your key
```

---

## Monthly cost breakdown

| Service | Cost |
|---|---|
| Railway Hobby plan | $5/mo |
| Cloudflare Pages | Free |
| Groq LLM | Free (rate-limited) |
| Voyage AI embeddings | Free (50M tokens) |
| Mistral OCR | Free tier |
| Razorpay | 2% per transaction only |
| **Total fixed** | **$5/mo** |
