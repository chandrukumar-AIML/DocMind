#!/bin/bash
# frontend/entrypoint.sh
# DVMELTSS-FIX: S - Security, E - Error handling
# ASCALE-FIX: S - Separation, E - Error propagation
set -euo pipefail

# ════════════════════════════════════════════════════════════════
# CONFIGURATION
# ════════════════════════════════════════════════════════════════
readonly NGINX_CONF="/etc/nginx/conf.d/default.conf"
readonly INDEX_HTML="/usr/share/nginx/html/index.html"
readonly HEALTH_TIMEOUT=30
readonly STARTUP_DELAY=2

# ════════════════════════════════════════════════════════════════
# LOGGING HELPER
# ════════════════════════════════════════════════════════════════
log() {
    echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*" >&2
}

# ════════════════════════════════════════════════════════════════
# RUNTIME ENV INJECTION (for Vite apps)
# ════════════════════════════════════════════════════════════════
inject_env_vars() {
    log "Injecting runtime environment variables..."

    # List of VITE_ vars to inject (add more as needed)
    local vars=(
        "VITE_API_URL"
        "VITE_APP_VERSION"
        "VITE_WS_URL"
        "VITE_ENABLE_ANALYTICS"
    )

    for var in "${vars[@]}"; do
        local value="${!var:-}"
        if [[ -n "$value" ]]; then
            # Replace placeholder in index.html (if using __VITE_API_URL__ style)
            if grep -q "__${var}__" "$INDEX_HTML" 2>/dev/null; then
                sed -i "s|__${var}__|${value}|g" "$INDEX_HTML"
                log "  ✓ Injected $var into index.html"
            fi
            # Also inject into a global JS object for runtime access
            echo "window.${var}='${value}';" >> /usr/share/nginx/html/env.js
        fi
    done

    # Create a simple env.js for runtime access (if not exists)
    if [[ ! -f /usr/share/nginx/html/env.js ]]; then
        echo "// Runtime environment variables" > /usr/share/nginx/html/env.js
        echo "// Injected at container startup" >> /usr/share/nginx/html/env.js
    fi
}

# ════════════════════════════════════════════════════════════════
# NGINX CONFIG VALIDATION
# ════════════════════════════════════════════════════════════════
validate_nginx() {
    log "Validating nginx configuration..."
    if ! nginx -t -c /etc/nginx/nginx.conf 2>&1; then
        log "❌ nginx configuration invalid"
        return 1
    fi
    log "✓ nginx configuration valid"
    return 0
}

# ════════════════════════════════════════════════════════════════
# HEALTH CHECK WAIT (for orchestrated deploys)
# ════════════════════════════════════════════════════════════════
wait_for_backend() {
    local backend_url="${BACKEND_HEALTH_URL:-http://backend:8000/health}"
    local retries=0
    local max_retries=10

    log "Waiting for backend at $backend_url..."

    while [[ $retries -lt $max_retries ]]; do
        if curl -sf "$backend_url" >/dev/null 2>&1; then
            log "✓ Backend is healthy"
            return 0
        fi
        retries=$((retries + 1))
        log "  Attempt $retries/$max_retries... retrying in 3s"
        sleep 3
    done

    log "⚠️ Backend not ready after $max_retries attempts (continuing anyway)"
    return 0 # Don't fail startup if backend is slow
}

# ════════════════════════════════════════════════════════════════
# MAIN ENTRYPOINT
# ════════════════════════════════════════════════════════════════
main() {
    log "🚀 Starting DocuMind frontend container"

    # Step 1: Inject runtime env vars
    inject_env_vars

    # Step 2: Validate nginx config
    if ! validate_nginx; then
        log "❌ Failed to validate nginx config"
        exit 1
    fi

    # Step 3: Wait for backend (optional, for orchestrated deploys)
    if [[ "${WAIT_FOR_BACKEND:-true}" == "true" ]]; then
        wait_for_backend
    fi

    # Step 4: Start nginx in foreground (required for Docker)
    log "✅ Starting nginx..."
    exec nginx -g "daemon off;"
}

# Run main function
main "$@"