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
# "prebuild" (scripts/public-assets.cjs) fails the build if a page points at an
# image that is not in public/. The list it reads from the source is then written
# out, so the final image can be checked against the same list.
RUN npm run build \
 && node scripts/public-assets.cjs --write /app/public-assets.txt

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
# server.js. Without it every file there (the site's screenshots, the share
# image) answered 404 and the image optimizer 400.
COPY --from=build --chown=app:app /app/public ./public
# Fail the build, not the live site, if an image the pages point at or the image
# library did not make it into this image. The list comes from the source (the
# build stage wrote it): no file is named here, so replacing a picture cannot
# leave this check asking for the old one.
COPY --from=build /app/public-assets.txt /tmp/public-assets.txt
RUN test -s /tmp/public-assets.txt \
 && while read -r asset; do test -s "public$asset" || { echo "frontend image is missing public$asset" >&2; exit 1; }; done < /tmp/public-assets.txt \
 && node -e "require('sharp')" \
 || (echo "frontend image is missing public/ assets or sharp" >&2; exit 1)
USER app
EXPOSE 3000
CMD ["node", "server.js"]
