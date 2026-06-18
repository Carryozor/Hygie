# ── Stage 1: Build Vue frontend ────────────────────────────────────────────────
FROM node:20-alpine AS frontend-builder
WORKDIR /build
COPY frontend/vue/package*.json ./
RUN npm ci
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

# Bundle frontend dependencies locally — no CDN needed at runtime.
# All downloads are integrity-checked: Font Awesome via sha256 pinning,
# dashboard icons via a pinned upstream commit (immutable jsdelivr URL).
RUN mkdir -p /app/frontend/static/css /app/frontend/static/webfonts \
    && curl -fsSL "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css" \
         -o /app/frontend/static/css/fa.min.css \
    && echo "c880eb3d25c765d399840aa204fec22b3230310991089f14781f09a35ed80b8a  /app/frontend/static/css/fa.min.css" | sha256sum -c - \
    && for f in \
         "fa-solid-900.woff2 f4c5a5b297e623bc159679563a4d1eb16e409ca3b57698fbc00fd2c907dadae0" \
         "fa-regular-400.woff2 3a74c08d486310c03731b458616f0172375fe3780e96165f8a1adc02d1355eaa" \
         "fa-brands-400.woff2 b66b3da5ff7b2db79b6cb5a22c3e762e2bf16958a11987e69eeb1980bbbcdfb0"; do \
         set -- $f; \
         curl -fsSL "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/webfonts/$1" \
           -o "/app/frontend/static/webfonts/$1" \
         && echo "$2  /app/frontend/static/webfonts/$1" | sha256sum -c - || exit 1; \
       done \
    && chown -R hygie:hygie /app/frontend/static/css /app/frontend/static/webfonts \
    && mkdir -p /app/frontend/static/img/icons \
    && for icon in \
         "radarr 2d1534f87513e2a7a3b699654dfed2ba70f6f65693ed4a015e497315135e2e67" \
         "sonarr a6d3283d0232b5a0bd0572f3d26edcbe64d07d36f43c3fc28d4f0bc155eaf49a" \
         "overseerr fe0ca048747082cb892b8a46d6ad605be10e09218a1ce2cc3cc65579123b1ebf" \
         "jellyseerr d3e5e5058a3e5c924107691445d91d7a9d8a09cba7003deb87c3c08006738b07" \
         "qbittorrent 3d03a2c91440a85ab6879207f90e17a8ff9c9580ae5a26ec3a64b2c2418f7019" \
         "discord 13ae2215f810ed44c9689dad38d622e5555dbc25c4eb347f46ad1c8a3539574c" \
         "emby 12c420023fd1f4e4738c4f43cb96b8bad0abb95f4b652261f5c8ec45006e71c2" \
         "jellyfin 2e45c7bb04b1fdbc70737be1676c73bcfbe98dcd9f72dcd063196c7075c65b37"; do \
         set -- $icon; \
         curl -fsSL "https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons@2c58e2cdcaadde7a65ee0e4f066eef4c84e9811a/png/$1.png" \
           -o "/app/frontend/static/img/icons/$1.png" \
         && echo "$2  /app/frontend/static/img/icons/$1.png" | sha256sum -c - || exit 1; \
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
