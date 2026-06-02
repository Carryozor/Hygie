# ── Stage 1: Build Vue frontend ────────────────────────────────────────────────
FROM node:20-alpine AS frontend-builder
WORKDIR /build
COPY frontend/vue/package*.json ./
RUN npm ci --prefer-offline
COPY frontend/vue/ ./
RUN npm run build
# Output: /build/../dist = /dist  (vite outDir: '../dist')

# ── Stage 2: Python backend ───────────────────────────────────────────────────
FROM python:3.12-slim@sha256:9d3abd9fc11d06998ccdbdd93b4dd49b5ad7d67fcbbc11c016eb0eb2c2194891

ARG VERSION=dev
# Set to "true" to embed MariaDB in the container (adds ~350MB — requires user: root at runtime)
ARG EMBEDDED_MARIADB_SUPPORT=false

ENV HYGIE_VERSION=$VERSION
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

LABEL org.opencontainers.image.title="Hygie" \
      org.opencontainers.image.description="Gestionnaire intelligent de bibliothèque média pour Emby" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.licenses="MIT"

# System dependencies: fonts for poster overlays, tini for clean shutdown, curl for asset bundling
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        fonts-dejavu-core \
        tini \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ── Optional: MariaDB server (embedded mode) ──────────────────────────────────
# Installed only when EMBEDDED_MARIADB_SUPPORT=true at build time.
# Usage: docker build --build-arg EMBEDDED_MARIADB_SUPPORT=true -t hygie:embedded .
ARG EMBEDDED_MARIADB_SUPPORT
RUN if [ "$EMBEDDED_MARIADB_SUPPORT" = "true" ]; then \
        apt-get update && \
        apt-get install -y --no-install-recommends \
            mariadb-server \
        && rm -rf /var/lib/apt/lists/* \
        && echo "[build] MariaDB server installed (embedded mode)"; \
    fi

# Create non-root user (UID/GID 1000 for bind-mount compatibility)
RUN groupadd -r -g 1000 hygie && \
    useradd -r -u 1000 -g hygie -d /app -s /sbin/nologin hygie && \
    mkdir -p /app/data && \
    chown -R hygie:hygie /app

WORKDIR /app

# Python dependencies (cached layer) — install as root, accessible to all
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY --chown=hygie:hygie backend/ /app/backend/
COPY --chown=hygie:hygie frontend/static/ /app/frontend/static/
COPY --chown=hygie:hygie frontend/templates/ /app/frontend/templates/

# Copy Vue dist from builder stage
COPY --from=frontend-builder --chown=hygie:hygie /dist/ /app/frontend/dist/

# Copy startup entrypoint
COPY --chown=hygie:hygie docker/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Bundle frontend dependencies locally — no CDN needed at runtime
RUN mkdir -p /app/frontend/static/css /app/frontend/static/webfonts \
    && curl -fsSL "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css" \
         -o /app/frontend/static/css/fa.min.css \
    && for f in fa-solid-900.woff2 fa-regular-400.woff2 fa-brands-400.woff2; do \
         curl -fsSL "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/webfonts/$f" \
           -o "/app/frontend/static/webfonts/$f"; \
       done \
    && chown -R hygie:hygie /app/frontend/static/css /app/frontend/static/webfonts \
    && mkdir -p /app/frontend/static/img/icons \
    && for icon in radarr sonarr overseerr jellyseerr qbittorrent discord emby jellyfin; do \
         curl -fsSL "https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/$icon.png" \
           -o "/app/frontend/static/img/icons/$icon.png" || true; \
       done \
    && chown -R hygie:hygie /app/frontend/static/img/icons

# Defensive permissions
RUN chmod -R a+rX /app/backend /app/frontend && \
    chmod 755 /app && \
    chmod 700 /app/data

# Default: run as non-root hygie user (SQLite + external MariaDB modes)
# For embedded MariaDB: override with 'user: root' in docker-compose
USER hygie

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python3 /app/backend/healthcheck.py

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/app/entrypoint.sh"]
