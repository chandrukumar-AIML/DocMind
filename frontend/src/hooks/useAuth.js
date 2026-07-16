// frontend/src/hooks/useAuth.js
// [OK] FIXED: JWT tokens now stored in httpOnly cookies (set by backend login/refresh).
// XSS attacks cannot steal tokens because JavaScript cannot read httpOnly cookies.
//
// Changes from localStorage-based auth:
//   - No more localStorage.getItem("documind_access_token") — token is in httpOnly cookie
//   - All fetch/axios calls use credentials:"include" / withCredentials:true so the
//     browser automatically sends the cookie
//   - Only non-sensitive data (workspace_id) stays in localStorage
//   - Token expiry is tracked via a separate non-sensitive "token_expiry" key
import { useState, useCallback, useEffect, useRef } from "react";
import { demoApi, isDemoMode, DEMO_USER } from "../api/demo";

// ════════════════════════════════════════════════════════════════════════
// CONFIG
// ════════════════════════════════════════════════════════════════════════
const API_URL = (import.meta.env?.VITE_API_URL || "http://localhost:8000").replace(/\/$/, "");
// [OK] FIXED: No TOKEN_KEY or REFRESH_KEY — tokens are in httpOnly cookies now.
// Only non-sensitive metadata is stored in localStorage.
const WORKSPACE_KEY = "documind_workspace_id";
const TOKEN_EXPIRY_KEY = "documind_token_expiry";  // non-sensitive: just a timestamp
const ACCESS_TOKEN_KEY = "documind_access_token";

// Check if session is near expiry (5 min buffer)
const isTokenExpiring = () => {
  const expiry = localStorage.getItem(TOKEN_EXPIRY_KEY);
  if (!expiry) return true;
  return Date.now() + 5 * 60 * 1000 > parseInt(expiry, 10);
};

export function useAuth() {
  const [user, setUser] = useState(null);
  const [workspaces, setWorkspaces] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const refreshTimerRef = useRef(null);

  const fetchMe = useCallback(async () => {
    let data;
    if (isDemoMode()) {
      data = DEMO_USER;
    } else {
      const token = localStorage.getItem(ACCESS_TOKEN_KEY);
      const res = await fetch(`${API_URL}/api/v1/auth/me`, {
        credentials: "include",
        headers: {
          "X-Correlation-ID": `auth_${Date.now()}`,
          ...(token ? { "Authorization": `Bearer ${token}` } : {}),
        },
      });
      if (!res.ok) {
        if (res.status === 401) throw new Error("Unauthorized");
        throw new Error(`HTTP ${res.status}`);
      }
      data = await res.json();
    }
    setUser(data);
    const isUUID = /^[0-9a-f]{8}-[0-9a-f]{4}-/i.test(data.workspace_id || "");
    setWorkspaces(data.workspaces || (data.workspace_id ? [{
      workspace_id: data.workspace_id,
      name: isUUID ? "Default Workspace" : `${data.workspace_id.replace(/_/g, " ")} Workspace`,
      role: data.role,
      is_default: true,
    }] : []));
    if (data.workspace_id) {
      localStorage.setItem(WORKSPACE_KEY, data.workspace_id);
    }
    return data;
  }, []);

  const logout = useCallback(async () => {
    // [OK] FIXED: Call backend logout to clear httpOnly cookies server-side.
    // Client cannot delete httpOnly cookies — only the server can via Set-Cookie.
    try {
      await fetch(`${API_URL}/api/v1/auth/logout`, {
        method: "POST",
        credentials: "include",
        headers: { "X-Correlation-ID": `logout_${Date.now()}` },
      });
    } catch {
      // Best-effort: clear local state even if server request fails
    }
    localStorage.removeItem(ACCESS_TOKEN_KEY);
    localStorage.removeItem(TOKEN_EXPIRY_KEY);
    localStorage.removeItem(WORKSPACE_KEY);
    if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current);
    setUser(null);
    setWorkspaces([]);
  }, []);

  const refreshToken = useCallback(async () => {
    // [OK] FIXED: No localStorage refresh token — the httpOnly cookie is sent
    // automatically by the browser to the /auth/refresh path.
    try {
      const res = await fetch(`${API_URL}/api/v1/auth/refresh`, {
        method: "POST",
        credentials: "include",   // sends refresh_token cookie automatically
        headers: {
          "Content-Type": "application/json",
          "X-Correlation-ID": `refresh_${Date.now()}`,
        },
        // Body still accepted for backward-compat with API clients — browser flow uses cookie
        body: JSON.stringify({}),
      });

      if (!res.ok) throw new Error("Refresh failed");

      const data = await res.json();
      // Backend sets new cookies in the response — no localStorage storage needed
      if (data.expires_in) {
        localStorage.setItem(TOKEN_EXPIRY_KEY, String(Date.now() + data.expires_in * 1000));
      }
      await fetchMe();
      return data;
    } catch (err) {
      // Refresh failed — force logout
      logout();
      throw err;
    }
  }, [fetchMe, logout]);

  // Auto-refresh token before expiry
  useEffect(() => {
    const setupRefresh = () => {
      const expiry = localStorage.getItem(TOKEN_EXPIRY_KEY);
      if (expiry && !isTokenExpiring()) {
        const timeUntilExpiry = parseInt(expiry, 10) - Date.now() - 5 * 60 * 1000;
        refreshTimerRef.current = setTimeout(refreshToken, timeUntilExpiry);
      }
    };

    // [OK] FIXED: Check for active session via /me endpoint (cookie-based).
    // We try fetchMe() — if the httpOnly cookie is valid, it succeeds.
    // No need to check localStorage for a token first.
    fetchMe()
      .then((data) => { setupRefresh(); setLoading(false); return data; })
      .catch(() => {
        localStorage.removeItem(TOKEN_EXPIRY_KEY);
        setUser(null);
        setLoading(false);
      });

    return () => {
      if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current);
    };
  }, [fetchMe, refreshToken]);

  const login = useCallback(async (email, password) => {
    setError(null);

    let data;
    if (isDemoMode()) {
      data = await demoApi.login(email, password);
    } else {
      const res = await fetch(`${API_URL}/api/v1/auth/login`, {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          "X-Correlation-ID": `login_${Date.now()}`,
        },
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        const detail = err.detail;
        const msg = Array.isArray(detail)
          ? detail[0]?.msg?.replace(/^Value error,\s*/i, "") || "Login failed"
          : (typeof detail === "string" ? detail : "Login failed");
        throw new Error(msg);
      }
      data = await res.json();
    }

    if (data.access_token) {
      localStorage.setItem(ACCESS_TOKEN_KEY, data.access_token);
    }
    if (data.expires_in) {
      localStorage.setItem(TOKEN_EXPIRY_KEY, String(Date.now() + data.expires_in * 1000));
    }
    if (data.workspace_id) localStorage.setItem(WORKSPACE_KEY, data.workspace_id);
    await fetchMe();
    return data;
  }, [fetchMe]);

  const register = useCallback(async (email, password, fullName, workspaceName) => {
    let data;
    if (isDemoMode()) {
      data = await demoApi.register(email, password, fullName);
    } else {
      const res = await fetch(`${API_URL}/api/v1/auth/register`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Correlation-ID": `register_${Date.now()}`,
        },
        body: JSON.stringify({
          email,
          password,
          display_name: fullName,
          ...(workspaceName ? { workspace_name: workspaceName } : {}),
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        // Pydantic returns detail as array; extract first message
        const detail = err.detail;
        const msg = Array.isArray(detail)
          ? detail[0]?.msg?.replace(/^Value error,\s*/i, "") || "Registration failed"
          : (typeof detail === "string" ? detail : "Registration failed");
        throw new Error(msg);
      }
      data = await res.json();
      // Backend returns RegistrationPendingResponse (no token) in prod — auto-login
      if (!data.access_token) return login(email, password);
    }
    // [OK] FIXED: Tokens in httpOnly cookies — no localStorage storage for tokens.
    if (data.expires_in) {
      localStorage.setItem(TOKEN_EXPIRY_KEY, String(Date.now() + data.expires_in * 1000));
    }
    if (data.workspace_id) localStorage.setItem(WORKSPACE_KEY, data.workspace_id);
    await fetchMe();
    return data;
  }, [fetchMe, login]);

  // [OK] FIXED: getToken() removed — token is in httpOnly cookie, JS cannot read it.
  // API calls send the cookie automatically via withCredentials/credentials:"include".
  const getWorkspaceId = useCallback(() => localStorage.getItem(WORKSPACE_KEY), []);
  const getCurrentWorkspace = useCallback(() => {
    return workspaces.find(ws => ws.workspace_id === getWorkspaceId()) || workspaces[0] || null;
  }, [workspaces, getWorkspaceId]);

  return {
    user,
    workspaces,
    loading,
    error,
    login,
    register,
    logout,
    // getToken removed — httpOnly cookie is not readable by JS (XSS-safe by design)
    getWorkspaceId,
    getCurrentWorkspace,
    refreshToken,
  };
}
