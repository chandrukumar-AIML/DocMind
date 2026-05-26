// frontend/src/utils/navigator.js
// DVMELTSS-FIX: M - Modular, S - Separation of concerns
// [OK] FIXED: Replaces window.location.href = "/login" hard navigation in axios interceptor.
//
// Problem: axios interceptor (client.js) is outside the React tree — it has no access
// to React Router's useNavigate() hook. Using window.location.href causes a full page
// reload which drops all React state (chat history, pending uploads, etc.).
//
// Solution: a mutable ref that AppRouter sets once on mount by calling setNavigator(navigate).
// The interceptor calls navigateTo() which uses the stored reference — soft navigation,
// no state loss, no full reload.
//
// Usage in AppRouter.jsx:
//   import { useEffect } from "react";
//   import { useNavigate } from "react-router-dom";
//   import { setNavigator } from "./utils/navigator";
//
//   export default function AppRouter() {
//     const navigate = useNavigate();
//     useEffect(() => { setNavigator(navigate); }, [navigate]);
//     ...
//   }
//
// Usage in client.js:
//   import { navigateTo } from "./utils/navigator";
//   navigateTo("/login");   // instead of window.location.href = "/login"

let _navigate = null;

/**
 * Register the React Router navigate function.
 * Call this from AppRouter on mount: setNavigator(navigate).
 */
export function setNavigator(navigateFn) {
  _navigate = navigateFn;
}

/**
 * Programmatic navigation using React Router.
 * Falls back to window.location.href if navigator not yet registered
 * (e.g., very early in the app lifecycle before AppRouter mounts).
 *
 * @param {string} path - Route path to navigate to (e.g., "/login")
 * @param {object} [options] - React Router navigate options (replace, state, etc.)
 */
export function navigateTo(path, options = {}) {
  if (_navigate) {
    _navigate(path, options);
  } else {
    // Fallback: only if navigator not registered yet (app not mounted)
    console.warn(`[navigator] React Router navigate not registered — falling back to hard redirect for: ${path}`);
    window.location.href = path;
  }
}
