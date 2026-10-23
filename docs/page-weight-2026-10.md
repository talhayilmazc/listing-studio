# Page weight, tunnel and uploads (23 October 2026)

Production was slow (pages, images, uploads), and visitors sometimes saw "SSL handshake failed".

## What the server showed

- It was not busy:
  - load 0.68;
  - 3.9 GB of RAM free;
  - the API's `/health` answered in 27 ms inside the server.
- Its internet is 69 Mbps down but only **14 Mbps up**. Everything a visitor loads leaves through that uplink.
- The cloudflared log showed three things:
  - "failed to accept QUIC stream: timeout: no recent network activity";
  - connections terminated and re-registered (`protocol=quic`, location ist04);
  - "Incoming request ended abruptly: context canceled" on `/login` and `/api/batches/<id>/assets`.

## What changed

1. **The tunnel runs HTTP/2, not QUIC.** It is also pinned to cloudflared 2026.10.3, restarted when it exits, and checked by preflight. Why: `docs/deploy.md`, "Tunnel protocol and routing".
2. **`/api/*` goes from the edge straight to the API**, no longer through the Next.js server. Same host, so the cookie, the CSRF check and the visitor's address are as before. The API now gzips its own JSON, which Next used to do.
3. **Screens get small copies of images**, WebP, made once and stored (`backend/app/pipeline/thumbs.py`):
   - `small` (240 px): tiles;
   - `thumb` (480 px): grids and cards;
   - `medium` (1200 px): viewers and editors.

   The full file is only for downloads, ZIPs and the Etsy upload.
4. **The browser keeps each image**, so it never downloads one twice (`backend/app/api/images.py`):
   - every image URL carries a version, and versioned answers are `private, max-age=31536000, immutable`;
   - every image has an ETag, answered with 304;
   - images below the fold load only when scrolled to.
5. **Uploads send three files at a time** (`frontend/lib/uploadQueue.ts`):
   - a file that fails on the way is tried again by itself;
   - "Try the failed ones again" sends only the failed files;
   - files go exactly as chosen, never resized in the browser.

   The server takes a photo's place in its batch under the batch's row lock. Before, six photos sent together got ranks 1, 2, 2, 2, 2, 2.
6. **Identical requests made at the same moment share one.** Overview used to ask for `/api/profiles` five times on each load.

## Measured

Method:

- **Stack:** the production images on a local machine. The production frontend image ran behind nginx, set up like the new tunnel rules ("after"), or alone ("before"). The API ran from the same code.
- **Data:** the synthetic demo account (`seed-demo --admin`), plus synthetic photo-like images: 24 at 2000 × 2500 in 8 hand-written listings, and a template of 6 at 2000 × 2400. Nothing real was used.
- **Browser:** Chromium through Playwright, its link throttled like the server's: 14 Mbps to the visitor, 69 Mbps from them, 40 ms latency.
- **Visits:** "first" is with an empty cache; "again" is the same page reloaded.
- **Time:** until the network was quiet. For the editor, until its photo was shown.
- **Upload:** 20 designs of 5.0 MB each through the real Upload designs page.

| Page | Visit | Bytes | Of which images | Requests | Load time at 14 Mbps |
|---|---|---|---|---|---|
| Overview | first | 485 → 476 KB | 9 → 9 KB | 79 → 73 | 1.04 → 0.98 s |
| Overview | again | 89 → 88 KB | 0 → 0 KB | 44 → 48 | 0.74 → 0.69 s |
| Batches | first | 562 → 512 KB | 81 → 35 KB | 89 → 87 | 1.19 → 1.25 s |
| Batches | again | 93 → 91 KB | 0 → 0 KB | 44 → 44 | 0.69 → 0.66 s |
| Batch page | first | 643 → 534 KB | 149 → 43 KB | 96 → 84 | 1.25 → 1.25 s |
| Batch page | again | 87 → 82 KB | 0 → 0 KB | 38 → 35 | 0.64 → 0.61 s |
| Review | first | 631 → 514 KB | 123 → 11 KB | 83 → 79 | 1.28 → 1.13 s |
| Review | again | 97 → 91 KB | 0 → 0 KB | 47 → 44 | 0.79 → 0.71 s |
| Mockup results | first | 644 → 539 KB | 172 → 69 KB | 101 → 99 | 1.28 → 1.22 s |
| Mockup results | again | 82 → 79 KB | 0 → 0 KB | 28 → 26 | 0.50 → 0.48 s |
| Templates | first | 472 → 465 KB | 41 → 35 KB | 71 → 69 | 0.97 → 0.94 s |
| Templates | again | 48 → 45 KB | 0 → 0 KB | 24 → 22 | 0.42 → 0.40 s |
| Template editor | first | 1,040 → 57 KB | 1,010 → 27 KB | 7 → 6 | 0.77 → 0.12 s |
| Template editor | again | 23 → 23 KB | 0 → 0 KB | 2 → 2 | 0.10 → 0.09 s |

**Upload of 20 designs of 5 MB** (100 MB): 21.7 s → **15.1 s**. Before, files went one at a time through Next; after, three at a time straight to the API. At 69 Mbps, 100 MB takes at least 11.6 s.

**The Next.js proxy by itself** (inside the Docker network, unthrottled, the same 20 files one at a time): 8.3 s through Next against 8.0 s straight to the API. The proxy streams, so it was never the main cost of an upload. Sending files three at a time is what made uploads faster. Routing past Node removes one more hop that can drop a request; that hop is where the "context canceled" lines came from.

What the numbers do not show:

- **The demo's images are lighter than real photos**, so the saving on real accounts is larger. The template editor loaded the full photo; for a real 2000 px template photo that is 1–2 MB, now about 30–60 KB at 1200 px.
- **The remaining weight of a first visit is the app's own scripts** (about 335 KB). They are public and immutable, so later visits take them from the cache.
- **About 40 API answers per page remain.** They are small (about 90 KB together, gzipped), and the duplicates made at the same moment are gone. Pages that ask once per listing card still do so (for example `/content/<id>/sku/etsy` and `/content/<id>/options` on the review page).

Reproduce: the scripts used are not part of the repository. They drive our own local server only, with the steps above.
