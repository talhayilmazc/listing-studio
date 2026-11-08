# Product screenshots on the public site

The screenshots in `frontend/public/screens/` are crops of the real app, signed
in to a **synthetic demo account that holds sample data only**: sample designs,
fictional shops ("Northwind Tees", "Juniper Print Co."), generated sales and fee
totals. No real seller's shop, design or figure appears in them, and they must
stay that way. Each one carries the label "Sample data".

## Retaking them

`frontend/scripts/marketing-shots.mjs` takes all ten, each cropped to the part that
matters (docs/design-v2.md) and saved at twice its size as WebP: once from the app's
wide layout (520 CSS px wide) and once from its phone layout (`-phone`, 359 px), so
the text reads at its natural size on a desktop and on a phone. Pass the server as
its argument (`node scripts/marketing-shots.mjs http://localhost:3000`). It refuses any account but the demo one and any server but a local one, and
hides the app's footer, which prints the support address of the machine it runs on.
Nothing in a crop may show a real e-mail address.

```bash
docker compose -f docker-compose.prod.yml build frontend
docker compose up -d --no-deps api
docker compose exec -T api python -m app.cli seed-demo --admin --shops 15   # prints the password
docker run -d --rm --name listyro-layout-frontend --network etsy-assistant_default -p 3000:3000 listyro/frontend:latest
cd frontend
LAYOUT_EMAIL=demo-shots@example.test LAYOUT_PASSWORD=<printed> node scripts/marketing-shots.mjs
cd ..
docker rm -f listyro-layout-frontend
docker compose exec -T api python -m app.cli seed-demo --remove
```

The last line matters: nothing about the demo account may be left in a database that
holds real sellers. Look at each picture before committing it.

Where they are used, with their natural sizes and alt text, is one file:
`frontend/lib/marketing/screens.ts`; a retaken crop of a different size needs its size changed there.
If a picture changes materially, give the file a new name there so no cache serves the
old one (`/screens/*` may be kept for a week at the edge).

## The share cards

Each public page has its own card, drawn at build time from the page's heading by
`frontend/app/og/[slug]/route.tsx`, in our own name and colours. There is nothing to draw
by hand. A heading changes, its card changes with the next build.

## The hero's demonstration

The home page opens with a demonstration built from the demo account's own data: design
NW1042, its title, its 13 tags and three of its shops (`frontend/lib/homeCopy.ts`, checked
against `backend/scripts/demo_screens_seed.py`). Its pictures are made by our own mockup
engine from that design on the demo's blank tees, not drawn:

```bash
docker compose exec -T api python - < backend/scripts/hero_assets.py
docker compose cp api:/tmp/hero/. frontend/public/hero/
```

The demo video slot was removed in site v2: the demonstration is the moment.
