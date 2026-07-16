// Vercel serverless function — pinged every 14 min by vercel.json cron
// Keeps Render free tier awake so first user request isn't slow.
export default async function handler(req, res) {
  try {
    const r = await fetch("https://docmind-backend-4ip1.onrender.com/health", {
      signal: AbortSignal.timeout(10000),
    });
    const data = await r.json();
    res.status(200).json({ pinged: true, backend: data.status, ts: new Date().toISOString() });
  } catch (e) {
    res.status(200).json({ pinged: false, error: e.message, ts: new Date().toISOString() });
  }
}
