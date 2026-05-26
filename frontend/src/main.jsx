// frontend/src/main.jsx
// DVMELTSS-FIX: E - Error handling, A - Accessibility, M - Modular
// ASCALE-FIX: S - Separation, L - Layered
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { ErrorBoundary } from './components/ErrorBoundary';
import './index.css';
import AppRouter from './AppRouter.jsx';

// ════════════════════════════════════════════════════════════════════════
// PRODUCTION READY: React 18 Root with Error Handling
// ════════════════════════════════════════════════════════════════════════

// Get root element with fallback
// [OK] FIXED: rootElement could be null if #root missing — createRoot(null) crashes.
// Reassign to fallback div so createRoot always receives a valid DOM element.
let rootElement = document.getElementById('root');

if (!rootElement) {
  // Fallback for SSR or missing element
  console.error('Root element not found. Creating fallback...');
  const fallback = document.createElement('div');
  fallback.id = 'root';
  fallback.innerHTML = '<div style="padding:2rem;text-align:center;color:#666">Loading...</div>';
  document.body.appendChild(fallback);
  rootElement = fallback; // [OK] FIXED: reassign so createRoot receives valid element
}

// Create root with React 18 concurrent features
const root = createRoot(rootElement);

// Render app with error boundary + strict mode
root.render(
  <StrictMode>
    <ErrorBoundary>
      <BrowserRouter>
        <AppRouter />
      </BrowserRouter>
    </ErrorBoundary>
  </StrictMode>
);

// ════════════════════════════════════════════════════════════════════════
// PRODUCTION OPTIMIZATIONS
// ════════════════════════════════════════════════════════════════════════

// Report web vitals for performance monitoring (optional)
if (import.meta.env?.PROD && 'reportWebVitals' in window) {
  // eslint-disable-next-line no-undef
  reportWebVitals((metric) => {
    // Send to analytics service (e.g., LangSmith, custom endpoint)
    console.debug('Web Vitals:', metric);
    // Example: fetch('/api/metrics', { method: 'POST', body: JSON.stringify(metric) });
  });
}

// Handle unhandled promise rejections globally
window.addEventListener('unhandledrejection', (event) => {
  console.error('Unhandled promise rejection:', event.reason);
  // Optional: Send to error tracking service
  // if (window.Sentry) Sentry.captureException(event.reason);
});

// Handle runtime errors globally (fallback if ErrorBoundary misses something)
window.addEventListener('error', (event) => {
  console.error('Global error:', event.error);
  // Optional: Send to error tracking service
  // if (window.Sentry) Sentry.captureException(event.error);
});