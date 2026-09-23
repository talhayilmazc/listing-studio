# Production frontend: built once, served by Next's standalone server.
# (docker/frontend.Dockerfile is the development image, run with `next dev`.)

# --- dependencies: exactly what package-lock.json pins ----------------------
FROM node:20-alpine AS deps
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

# --- build ------------------------------------------------------------------
FROM node:20-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY frontend/ ./
# Rewrites are resolved at build time, so the backend's in-network address is
# a build argument, not a runtime variable.
ARG API_PROXY_TARGET=http://api:8000
ENV API_PROXY_TARGET=$API_PROXY_TARGET \
    NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=production
RUN npm run build

# --- runtime: the standalone server and nothing else -------------------------
FROM node:20-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
RUN addgroup -S -g 1001 app && adduser -S -u 1001 -G app app
COPY --from=build --chown=app:app /app/.next/standalone ./
COPY --from=build --chown=app:app /app/.next/static ./.next/static
# (No public/ directory: fonts are self-hosted by next/font into .next/static.)
USER app
EXPOSE 3000
CMD ["node", "server.js"]
