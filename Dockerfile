# syntax=docker/dockerfile:1
###############################################################################
# Unified zero-cost deployment image
#   * Node 20 gateway on private loopback :3000
#   * Python 3.11 aiogram service on the host-provided public $PORT
###############################################################################
FROM node:20-bookworm-slim AS node-runtime

FROM python:3.11-slim-bookworm AS wheel-builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY bots/requirements.txt .
RUN pip wheel --wheel-dir /wheels -r requirements.txt

FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/bots \
    NODE_ENV=production \
    NODE_OPTIONS=--max-old-space-size=192 \
    GATEWAY_PORT=3000 \
    GATEWAY_URL=http://127.0.0.1:3000 \
    WEB_HOST=0.0.0.0 \
    PORT=7860

# Node's official binary is copied from the matching Debian image. libstdc++ is
# its only non-base runtime library; tini + bash provide supervision/signals.
RUN apt-get update \
 && apt-get install -y --no-install-recommends bash ca-certificates libatomic1 libstdc++6 tini \
 && rm -rf /var/lib/apt/lists/*
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node

WORKDIR /app
COPY --from=wheel-builder /wheels /wheels
COPY bots/requirements.txt /app/bots/requirements.txt
RUN pip install --no-index --find-links=/wheels -r /app/bots/requirements.txt \
 && rm -rf /wheels

COPY gateway /app/gateway
COPY bots /app/bots
COPY entrypoint.sh /app/entrypoint.sh

RUN useradd --create-home --shell /bin/bash konkred \
 && chmod 0755 /app/entrypoint.sh \
 && chown -R konkred:konkred /app
USER konkred

# Hugging Face Docker Spaces uses 7860 by default. Render ignores EXPOSE and
# supplies its own PORT, which the Python public server reads dynamically.
EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD-SHELL python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','7860')+'/healthz', timeout=3)" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/app/entrypoint.sh"]
