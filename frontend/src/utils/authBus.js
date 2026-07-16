// frontend/src/utils/authBus.js
// Allows code outside the React tree (e.g. axios interceptor) to trigger logout.
// useAuth registers its logout function here on mount via setAuthClearer().

let _logout = null;

export function setAuthClearer(logoutFn) {
  _logout = logoutFn;
}

export async function clearAuth() {
  if (_logout) {
    await _logout();
  }
}
