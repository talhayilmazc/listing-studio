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
# The standalone output leaves public/ out on purpose; it must be copied beside
# server.js. Without it every file there (the site's screenshots, og.png)
# answered 404 and the image optimizer 400.
COPY --from=build --chown=app:app /app/public ./public
# Fail the build, not the live site, if the screenshots or the image library
# did not make it into the image.
RUN test -s public/og-v2.png \
 && test -n "$(ls public/screens/*.webp 2>/dev/null)" \
 && node -e "require('sharp')" \
 || (echo "frontend image is missing public/ assets or sharp" >&2; exit 1)
USER app
EXPOSE 3000
CMD ["node", "server.js"]
