FROM python:3.12-slim@sha256:9d3abd9fc11d06998ccdbdd93b4dd49b5ad7d67fcbbc11c016eb0eb2c2194891

ARG VERSION=dev
ENV HYGIE_VERSION=$VERSION
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

LABEL org.opencontainers.image.title="Hygie" \
      org.opencontainers.image.description="Gestionnaire intelligent de bibliothèque média pour Emby" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.licenses="MIT"

# System dependencies: fonts for poster overlays, tini for clean shutdown
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        fonts-dejavu-core \
        tini \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user FIRST (UID/GID 1000 for bind-mount compatibility)
RUN groupadd -r -g 1000 hygie && \
    useradd -r -u 1000 -g hygie -d /app -s /sbin/nologin hygie && \
    mkdir -p /app/data && \
    chown -R hygie:hygie /app

WORKDIR /app

# Python dependencies (cached layer) — install as root, accessible to all
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code — copied with explicit ownership to hygie user
COPY --chown=hygie:hygie backend/ /app/backend/
COPY --chown=hygie:hygie frontend/ /app/frontend/

# Defensive permissions — readable by all users, writable only by owner
# (covers cases where bind mounts or umask quirks would block access)
RUN chmod -R a+rX /app/backend /app/frontend && \
    chmod 755 /app && \
    chmod 700 /app/data

USER hygie

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python3 /app/backend/healthcheck.py

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
