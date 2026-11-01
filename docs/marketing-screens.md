# Product screenshots on the public site

The screenshots in `frontend/public/screens/` are crops of the real app, signed
in to a **synthetic demo account that holds sample data only**: sample designs,
fictional shops ("Northwind Tees", "Juniper Print Co."), generated sales and fee
totals. No real seller's shop, design or figure appears in them, and they must
stay that way. Each one carries the label "Sample data".

## Retaking them

`frontend/scripts/marketing-shots.mjs` takes all eight, 4:3, at 1120 and 560 wide,
as WebP. It refuses any account but the demo one and any server but a local one, and
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

Where they are used, with their alt text, is one file: `frontend/lib/marketing/screens.ts`.
If a picture changes materially, give the file a new name there so no cache serves the
old one (`/screens/*` may be kept for a week at the edge).

## The share cards

Each public page has its own card, drawn at build time from the page's heading by
`frontend/app/og/[slug]/route.tsx`, in our own name and colours. There is nothing to draw
by hand. A heading changes, its card changes with the next build.

## The demo video

`frontend/lib/marketing/video.ts`. Until the file is set, the home page shows a screenshot
and says the video is coming.
