# syntax=docker/dockerfile:1
###############################################################################
# Konkred multi-bot daemon - Python 3.11, all five bots in one process.
#
# Two-stage build: wheels are compiled in the builder stage so the runtime image
# carries no compiler toolchain.
###############################################################################
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip wheel --wheel-dir /wheels -r requirements.txt


###############################################################################
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# tini provides correct signal handling so SIGTERM reaches the asyncio loop and
# main.py can close bot sessions, the HTTP pool and Redis cleanly.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tini \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt \
 && rm -rf /wheels

COPY . .

# Run unprivileged.
RUN useradd --create-home --shell /bin/bash konkred \
 && chown -R konkred:konkred /app
USER konkred

# Liveness: the daemon is healthy while the process can import its own config
# and resolve at least the module graph (polling itself has no HTTP surface).
HEALTHCHECK --interval=60s --timeout=10s --start-period=20s --retries=3 \
  CMD python -c "import shared.config, shared.gateway_client" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "main.py"]
