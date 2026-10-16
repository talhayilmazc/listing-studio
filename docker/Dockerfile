FROM python:3.12-slim

# MALLOC_ARENA_MAX: glibc otherwise gives every thread (libvips, onnxruntime, the
# worker's job threads) its own heap, and memory freed after a template analysis is
# not reused: measured, the worker's peak grew from 1.3 GB to 2.2 GB over 70
# templates; with 2 arenas it stays at 1.7 GB, same speed.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    MALLOC_ARENA_MAX=2

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
# sympy (and mpmath) come with onnxruntime only for its model-conversion tools; running a
# model does not need them (pipeline/garments.py), so they are removed (~80 MB).
RUN pip install --upgrade pip \
    && if [ -n "$EXTRAS" ]; then pip install ".[$EXTRAS]"; else pip install .; fi \
    && pip uninstall -y sympy mpmath

# The image-analysis models for mockup templates (pipeline/mockup_vision.py,
# pipeline/garments.py), fetched now, at build time, and checked against their SHA-256:
# nothing is downloaded at runtime and no photo leaves the server. Apache-2.0 (MediaPipe, IS-Net).
COPY backend/app/pipeline/fetch_models.py /tmp/fetch_models.py
RUN python /tmp/fetch_models.py /opt/listyro/models && rm /tmp/fetch_models.py
ENV MOCKUP_MODELS_DIR=/opt/listyro/models

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
