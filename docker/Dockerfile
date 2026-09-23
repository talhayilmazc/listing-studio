FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# libvips runtime for pyvips (image processing). Kept minimal.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libvips42 \
    && rm -rf /var/lib/apt/lists/*

# Optional dependency groups. Production builds install none — no test
# runner, no type checker in the running image. docker-compose.yml passes
# "dev" for local work so the suite can run inside the container.
ARG EXTRAS=""

# Install dependencies first for better layer caching.
# Only the project metadata is needed to resolve deps.
COPY backend/pyproject.toml ./pyproject.toml
RUN pip install --upgrade pip \
    && if [ -n "$EXTRAS" ]; then pip install ".[$EXTRAS]"; else pip install .; fi

# Application code is bind-mounted in docker-compose for local dev, and baked in
# for production (docker-compose.prod.yml mounts nothing over it).
COPY backend/ ./

# Run as non-root. Create the storage dir owned by appuser so the mounted
# named volume seeds with writable ownership.
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /data/storage \
    && chown -R appuser:appuser /app /data/storage
USER appuser

EXPOSE 8000

# No "server: uvicorn" header advertising the stack.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
