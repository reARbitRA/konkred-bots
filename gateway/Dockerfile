# syntax=docker/dockerfile:1
###############################################################################
# Konkred AI Gateway - Node.js 20 ESM, zero runtime dependencies.
#
# The image installs nothing from npm at runtime (package.json has no
# dependencies), so the build is fully reproducible and offline-friendly.
###############################################################################
FROM node:20-alpine

# tini gives us correct PID 1 signal forwarding; wget backs the healthcheck.
RUN apk add --no-cache tini wget

ENV NODE_ENV=production \
    PORT=3000 \
    HOST=0.0.0.0 \
    NODE_OPTIONS=--max-old-space-size=384

WORKDIR /app

# Copy the manifest first so this layer caches independently of the sources.
COPY package.json ./
# There are deliberately zero runtime dependencies, so there is nothing to
# install - no npm registry round-trip, no node_modules, no lockfile drift.
# Assert that stays true rather than silently shipping an unvetted dependency.
RUN node -e "const p=require('./package.json'); \
    const d=Object.keys(p.dependencies||{}); \
    if(d.length){console.error('unexpected runtime dependencies: '+d.join(', '));process.exit(1);} \
    console.log('zero runtime dependencies confirmed');"

COPY src ./src
COPY data ./data

# The watchdog persists runtime.state.json next to the registry, so the data
# directory must be writable by the unprivileged runtime user.
RUN mkdir -p /app/data && chown -R node:node /app

USER node
EXPOSE 3000

# Matches the compose healthcheck; kept here so `docker run` is also monitored.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget --quiet --tries=1 --spider http://127.0.0.1:3000/api/health || exit 1

ENTRYPOINT ["/sbin/tini", "--"]
CMD ["node", "src/server.mjs"]
