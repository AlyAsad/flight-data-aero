# Aero: single container (FastAPI serves the API and the built frontend).
# Build: docker build -t aero .    Run: docker run -p 8000:8000 --env-file .env aero

# ---- 1. Build the frontend
FROM node:22-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- 2. Python runtime
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
COPY --from=frontend /app/frontend/dist /app/frontend/dist

# Run as an unprivileged user (uid 1000 also suits Hugging Face Spaces).
RUN useradd --create-home --uid 1000 aero && mkdir -p /app/data && chown -R aero /app/data
USER aero

# Cloud Run (and most hosts) inject PORT; 8000 is the local default.
ENV PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/api/status', timeout=4)"
# --timeout-graceful-shutdown: open live-update streams would otherwise delay shutdown
# past Cloud Run's 10 s SIGTERM window, and the final cache snapshot would be lost.
CMD ["sh", "-c", "exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*' --timeout-graceful-shutdown 3"]
