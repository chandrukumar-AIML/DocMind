# DocuMind AI — Frontend

React 19 + Vite frontend for the DocuMind AI document intelligence platform.

## Development

```bash
npm install
cp .env.local.example .env.local   # set VITE_API_URL if needed
npm run dev
# Open http://localhost:5173
```

## Build

```bash
npm run build        # production build → dist/
npm run preview      # preview prod build locally
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `VITE_API_URL` | `http://localhost:8000` | Backend API URL |
| `VITE_DEMO_MODE` | `false` | Set true for demo (no backend needed) |

## Stack

- **React 19** + **Vite 8** — Framework + build tool
- **Axios** — HTTP client with JWT auto-inject + 401 auto-refresh interceptors
- **react-hot-toast** — Notifications
- **react-markdown** — Markdown rendering for AI answers
- **Nginx** — Production static file serving (Docker)

## Key Features

- 6 sidebar tabs: DOCS, ANALYZE, HISTORY, TRAIN, STATS, FEATURES
- 10 FEATURES sub-panels: Webhooks, Compare, Workflows, Annotate, Templates, E-Sign, Compliance, Admin, Onboard, Regional
- Streaming chat with real-time token display (SSE)
- RAG / Agent / Graph query modes
- JWT auto-refresh (15-min access + 30-day refresh via httpOnly cookie)
