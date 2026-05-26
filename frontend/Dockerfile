# frontend/Dockerfile
# DVMELTSS-FIX: S - Security, M - Modular, P - Performance
# ASCALE-FIX: L - Layered, S - Separation
# BATMAN-FIX: M - Memory safety, A - Async-ready

# ════════════════════════════════════════════════════════════════
# Stage 1: Node builder — Install deps + build
# ════════════════════════════════════════════════════════════════
FROM node:20-alpine AS builder

# Security: Run as non-root during build
USER node

WORKDIR /build

# Copy package files first for better layer caching
COPY --chown=node:node package.json package-lock.json ./

# Install dependencies with security flags
RUN npm ci --prefer-offline --no-audit --no-fund

# Copy source code
COPY --chown=node:node . .

# Build with environment variables passed at build time (optional)
# Note: VITE_ vars are embedded at build time; runtime vars use entrypoint.sh
ARG VITE_API_URL
ARG VITE_APP_VERSION
ENV VITE_API_URL=${VITE_API_URL}
ENV VITE_APP_VERSION=${VITE_APP_VERSION}

RUN npm run build

# Verify build output exists
RUN test -d /build/dist && echo "✅ Build successful" || exit 1

# ════════════════════════════════════════════════════════════════
# Stage 2: nginx runtime — Serve static files + proxy API
# ════════════════════════════════════════════════════════════════
FROM nginx:1.25-alpine AS runtime

# Security: Install only needed packages
RUN apk add --no-cache bash curl tini

# Create non-root user for nginx worker processes
RUN addgroup -g 1001 -S nginx-workers && \
    adduser -u 1001 -S nginx-worker -G nginx-workers

# Remove default nginx config
RUN rm /etc/nginx/conf.d/default.conf

# Copy nginx config with API proxy + security headers
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy built assets from builder
COPY --from=builder --chown=nginx-worker:nginx-workers /build/dist /usr/share/nginx/html

# Copy entrypoint script for runtime env injection
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && chown root:root /entrypoint.sh

# Health check for orchestration
HEALTHCHECK \
    --interval=30s \
    --timeout=5s \
    --start-period=10s \
    --retries=3 \
    CMD curl -f http://localhost:80/health || exit 1

# Expose port
EXPOSE 80

# Use tini as init system for proper signal handling
ENTRYPOINT ["/sbin/tini", "--", "/entrypoint.sh"]