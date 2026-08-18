# Frontend — Listing Studio

Next.js (App Router) + TypeScript + Tailwind. Runs at http://localhost:3000.

## Screens

- `/upload` — drag-and-drop a folder of design images; creates an upload batch with per-file progress.
- `/batches/[id]` — batch view: thumbnails, parsed SKU, rank, asset status; "Generate content" and cost panel.
- `/batches/[id]/review` — the core screen: edit the generated title, 13 tags and description inline, with an approve toggle. Publishing to Etsy is disabled (arrives with the Etsy client step).
- `/terms`, `/privacy` — placeholder compliance pages.

## Running

Via docker-compose (recommended): `docker compose up` → http://localhost:3000. The Next dev server proxies `/api/*` to the FastAPI backend (`API_PROXY_TARGET`, default `http://api:8000`), so the browser stays same-origin.

Standalone: `npm install && npm run dev` (set `API_PROXY_TARGET=http://localhost:8000` if the API runs elsewhere).

## Compliance elements in the UI (from CLAUDE.md)

- The exact Etsy trademark notice, Terms/Privacy links, and a visible support email live in the footer (`components/Footer.tsx`).
- Remaining daily API quota is shown in the top bar (`components/QuotaBadge.tsx`).
- The app name and title do not contain the word "Etsy".
