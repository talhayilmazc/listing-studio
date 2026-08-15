# Etsy Listing Assistant

Multi-tenant SaaS that helps Etsy sellers prepare **draft listings** from their own
original designs. Closed beta. See [CLAUDE.md](CLAUDE.md) for the full spec and the
absolute constraints (no scraping, no competitor analysis, no auto-publish, no Etsy Ads).

This repository currently contains the **project skeleton only** — folder structure,
docker-compose (Postgres + Redis + API + arq worker), a FastAPI health endpoint, and
Alembic scaffolding. No business logic yet.

## Stack

- Backend: Python 3.12, FastAPI, SQLAlchemy 2.x, Alembic
- Queue: Redis + [arq](https://arq-docs.helpmanual.io/)
- DB: PostgreSQL
- Frontend: Next.js (App Router) + TypeScript + Tailwind — *scaffolded later*

## Running locally

```bash
cp .env.example .env      # then edit values
docker compose up --build
```

Services:

| Service    | Port | Notes                                  |
| ---------- | ---- | -------------------------------------- |
| `api`      | 8000 | FastAPI (`/health`, `/docs`)           |
| `postgres` | 5432 | PostgreSQL 16                          |
| `redis`    | 6379 | Redis 7 (arq broker)                   |
| `worker`   | —    | arq worker (no jobs registered yet)    |

Health check:

```bash
curl http://localhost:8000/health   # -> {"status":"ok"}
```

## Tests

```bash
cd backend
pip install -e ".[dev]"
pytest
```

## Notes

- Secrets live in `.env` (gitignored); only `.env.example` is committed.
- Caddy reverse proxy (production deploy) is deferred to a later step.

---

The term "Etsy" is a trademark of Etsy, Inc. This Application uses Etsy's API, but is
not endorsed or certified by Etsy.
