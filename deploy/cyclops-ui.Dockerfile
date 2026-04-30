# Cyclops-UI image. Build context is the repo root so we can access
# both monorepo packages:
#
#   docker compose -f docker-compose.yml -f docker-compose.staging.yml build cyclops-ui
#
# (compose context: ../ from this Dockerfile's directory).

FROM python:3.12-slim

WORKDIR /app

# Copy both monorepo packages and install in dependency order — the
# cyclops library is installed first so `cyclops_ui` resolves the
# `cyclops` import without pulling from PyPI.
COPY packages/cyclops /app/cyclops
COPY packages/cyclops-ui /app/cyclops-ui

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir /app/cyclops /app/cyclops-ui gunicorn

EXPOSE 8000

# Liveness probe — cyclops-ui exposes /health unauthenticated; Caddy
# bypasses gatekeeper for that path so external probes work too.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request, sys; \
sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=3).status == 200 else 1)" \
        || exit 1

CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:8000", "cyclops_ui.app:app"]
