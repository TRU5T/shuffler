# --- stage 1: build the SPA ------------------------------------------------
FROM node:20-alpine AS frontend

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund 2>/dev/null || npm install --no-audit --no-fund

COPY frontend/ ./
RUN npm run build


# --- stage 2: runtime ------------------------------------------------------
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="Shuffler" \
      org.opencontainers.image.description="See where a folder is scattered across Unraid data disks and consolidate it safely" \
      org.opencontainers.image.source="https://github.com/TRU5T/shuffler" \
      net.unraid.docker.webui="http://[IP]:[PORT:8756]/" \
      net.unraid.docker.icon="https://raw.githubusercontent.com/TRU5T/shuffler/main/assets/logo.png"

# findutils and coreutils give the local backend the same GNU tools the SSH
# backend relies on remotely; rsync is handy for manual recovery.
RUN apt-get update \
    && apt-get install -y --no-install-recommends findutils coreutils tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY scripts/ ./scripts/
COPY --from=frontend /build/dist ./frontend/dist

ENV PYTHONPATH=/app/backend \
    PYTHONUNBUFFERED=1 \
    SHUFFLER_STORAGE_BACKEND=local \
    SHUFFLER_MOUNT_ROOT=/mnt \
    SHUFFLER_DB_PATH=/config/shuffler.db \
    SHUFFLER_HOST=0.0.0.0 \
    SHUFFLER_PORT=8756 \
    SHUFFLER_DRY_RUN=true

# Holds the SQLite database: scans, the queue and connection settings.
VOLUME ["/config"]

EXPOSE 8756

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8756/api/version', timeout=4).status == 200 else 1)"

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8756", "--app-dir", "/app/backend"]
