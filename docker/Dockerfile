FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# libvips runtime for pyvips (image processing). Kept minimal.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libvips42 \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first for better layer caching.
# Only the project metadata is needed to resolve deps.
COPY backend/pyproject.toml ./pyproject.toml
RUN pip install --upgrade pip && pip install ".[dev]"

# Application code is bind-mounted in docker-compose for local dev,
# but copy it so the image is runnable standalone too.
COPY backend/ ./

# Run as non-root.
RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
