# The public site: search and caching

Written for whoever operates listyro.com. The code side is done; the steps marked **You** are done by
hand in the Cloudflare, Google and Bing dashboards.

## What the code does

- `frontend/lib/marketing/routes.mjs` is the one list of public pages and the cache policy. Everything
  not listed there is `private, no-store`. A page is public only when the request has no `session`
  cookie and no Next router marker. `next.config.mjs` builds its header rules from this file.
- `frontend/e2e/headers.spec.ts` reads the real headers of every page, including every app page listed
  from the folders, on every browser run. `backend/tests/test_security.py` does the same for every API route.
- `sitemap.xml` and `robots.txt` come from the same registry (`frontend/lib/marketing/pages.ts`).

| Route | `Cache-Control` | Notes |
|---|---|---|
| Marketing pages, signed out | `public, s-maxage=3600, stale-while-revalidate=86400` | Static HTML; no `Set-Cookie` |
| Marketing pages with the `session` cookie | `private, no-store` | Any cookie value |
| Marketing pages with an `RSC` header or `?_rsc=` | `private, no-store` | The router's payload must never replace the page |
| `/` with a live session | `private, no-store` | 307 to `/dashboard` |
| `/` with a dead session cookie | `private, no-store` | Landing page; the cookie is cleared |
| `/screens/*`, `/og/*`, `/icon.svg`, `/favicon.ico`, `/robots.txt`, `/sitemap.xml` | `public, max-age=86400, s-maxage=604800, stale-while-revalidate=86400` | |
| `/_next/static/*` | `public, max-age=31536000, immutable` | Next's own hashed files |
| Every app page (`/dashboard`, `/batches`, `/login`, …) | `private, no-store` | Before this change they sent `s-maxage=31536000` |
| Any address nobody listed; 404s | `private, no-store` (Next's 404 adds `no-cache`, `max-age=0`) | |
| `/api/*` JSON and errors | `private, no-store` | Set by the API's middleware |
| `/api/*` images (versioned URL) | `private, max-age=31536000, immutable` | Kept on purpose (page weight, 14 Mbps). `private`: only the signed-in browser may keep it |
| `/api/*` images (no version) | `private, no-cache` | Same |

## The Cloudflare Cache Rule

**You:** Cloudflare dashboard → listyro.com → **Caching** → **Cache Rules** → **Create rule**.

Rule name: `Listyro public pages`

**Expression** (choose *Edit expression* and paste it):

```
(http.host eq "listyro.com")
and not (http.cookie contains "session=")
and not starts_with(http.request.uri.path, "/api/")
and (
  http.request.uri.path in {
    "/" "/features" "/pricing" "/free-tools" "/compare" "/guides" "/help"
    "/request-invite" "/contact" "/terms" "/privacy"
    "/robots.txt" "/sitemap.xml" "/icon.svg" "/favicon.ico"
  }
  or starts_with(http.request.uri.path, "/features/")
  or starts_with(http.request.uri.path, "/guides/")
  or starts_with(http.request.uri.path, "/compare/")
  or starts_with(http.request.uri.path, "/screens/")
  or starts_with(http.request.uri.path, "/og/")
)
and not http.request.uri.query contains "_rsc"
```

No app path appears in it, and `/api/` is shut out twice. An app route that is added later stays out
unless someone adds it here.

**Then:**

- **Cache eligibility:** Eligible for cache
- **Edge TTL:** *Use cache-control header if present, bypass cache if not*
- **Browser TTL:** *Respect origin*
- Leave everything else as it is. Do **not** set "Ignore cache-control header": the origin's `private,
  no-store` is the last safety net, and that option would remove it.

### If Cloudflare refuses the cookie condition on your plan

Cloudflare's documentation says `http.request.cookies` needs Pro or above. It lists `http.cookie` in the
Cache Rules builder with no plan limit, but does not say outright that the Free plan has it.

If the dashboard refuses it, use this instead and accept one small change: remove the `http.cookie` line
and remove `"/"` from the list. The other public pages do not depend on who is asking at all. Only `/` does
(it sends a signed-in visitor to the dashboard), so `/` then simply goes to the origin every time.

Why this is safe either way: the origin marks any request with the cookie `private, no-store`, so even
with no cookie condition in the rule, such a response is not stored. But a signed-out visitor's stored
copy could be served to a signed-in one, who would see the landing page instead of being sent to the
dashboard. Keeping `/` out of the rule avoids that.

### After each deploy

**You:** Cloudflare → **Caching** → **Configuration** → **Purge Everything**.

A cached page names JavaScript files by hash. After a deploy the old files are gone from the origin, so a
page kept from before the deploy could fail to load its script (the invite form and the plan switch need
it) until it expires, up to an hour later. Purging takes a few seconds and costs only a burst of misses.

## How to check it works

Run these after adding the rule. The second request of each pair should show the result.

**Signed out, a public page: must become `HIT`.**

```bash
curl -sI https://listyro.com/pricing | grep -iE 'cf-cache-status|cache-control|set-cookie'
curl -sI https://listyro.com/pricing | grep -iE 'cf-cache-status|cache-control|set-cookie'
```

Expect `cf-cache-status: HIT` on the second, `cache-control: public, s-maxage=3600, stale-while-revalidate=86400`,
and **no** `set-cookie`.

**With a session cookie: must not be cached.**

```bash
curl -sI -H 'Cookie: session=check' https://listyro.com/pricing | grep -iE 'cf-cache-status|cache-control'
```

Expect `cf-cache-status: DYNAMIC` (the rule did not match) and `cache-control: private, no-store`.

**An app page and the API: must never be cached.**

```bash
curl -sI https://listyro.com/dashboard | grep -iE 'cf-cache-status|cache-control'
curl -sI https://listyro.com/api/meta  | grep -iE 'cf-cache-status|cache-control'
```

Expect `DYNAMIC` and `private, no-store` on both.

**The router's payload: must not replace the page.**

```bash
curl -sI -H 'RSC: 1' https://listyro.com/pricing | grep -iE 'cf-cache-status|cache-control|content-type'
curl -s https://listyro.com/pricing | head -c 60
```

The first may show `HIT` if it was answered from the stored page, or `BYPASS` with `private, no-store` if
it reached the origin. The second must start with `<!DOCTYPE html>`, always. If it ever starts with
anything else, turn the rule off and purge: the router's payload has been stored.

**In a browser, signed in:** open `https://listyro.com/` and you should land on your dashboard. Open
`/dashboard` and check DevTools → Network → the document: `cf-cache-status: DYNAMIC`, `cache-control:
private, no-store`. Then open an account page in a private window, signed out, and confirm you are sent to
sign in. Nothing of yours should appear.

What `cf-cache-status` means: `HIT` from the edge; `MISS` eligible, fetched from us this time; `EXPIRED`
was stored, had expired, fetched again; `REVALIDATED` still current after asking us; `BYPASS` eligible, but
we said not to store it; `DYNAMIC` the rule did not match. On any app or API address, **`HIT` is an incident**:
turn the rule off, purge, and read `docs/security-incident.md`.

One thing to expect: Cloudflare's documentation says `s-maxage` disables `stale-while-revalidate`, so at
the edge a page simply revalidates after an hour. That costs one short answer from us. Other caches honour
the longer window.

## The visit counter (Cloudflare Web Analytics)

Cookieless, so no banner. It runs on public pages only, never in the app, and only when its token is set.

1. **You:** Cloudflare → **Analytics & Logs** → **Web Analytics** → **Add a site** → `listyro.com`. Choose
   the manual JS snippet, not automatic setup: automatic setup injects it on every page, app included.
2. **You:** copy only the token from the snippet (`{"token": "…"}`) and add this line to `.env` on the server:
   `NEXT_PUBLIC_CF_ANALYTICS_TOKEN=<token>`
3. The next `bash deploy/update.sh` builds it in. It is not a secret: it appears in every public page's HTML.

Empty means no counter and no extra origin in the Content-Security-Policy. To check: View Source on `/pricing`
shows `static.cloudflareinsights.com/beacon.min.js`, and on `/login` it does not.

## Google Search Console

1. **You:** go to https://search.google.com/search-console → **Add property** → **Domain** → `listyro.com`.
2. Google shows a TXT record. **You:** Cloudflare → **DNS** → **Records** → **Add record**: type `TXT`, name
   `@`, content the value Google gave. Save.
3. Back in Search Console press **Verify**. It can take a few minutes.
4. **Sitemaps** → enter `https://listyro.com/sitemap.xml` → **Submit**.
5. **URL inspection** → paste `https://listyro.com/` → **Request indexing**. Repeat for `/pricing` and two
   or three guides; the rest follow from the sitemap.

## Bing Webmaster Tools

1. **You:** go to https://www.bing.com/webmasters → sign in.
2. Easiest: **Import from Google Search Console**, which brings the site and sitemap, once step 4 above is done.
3. Otherwise **Add a site** → `https://listyro.com/` → verify with the **CNAME** or **TXT** record it shows,
   added in Cloudflare DNS as above (for a CNAME, set the proxy status to *DNS only*).
4. **Sitemaps** → **Submit sitemap** → `https://listyro.com/sitemap.xml`.

## Keeping it true

- **A new public page:** add it to `routes.mjs` and to the registry `pages.ts`. A test fails until both agree.
  If its path is a new top-level one, add it to the Cloudflare rule's list too.
- **Guides and compare pages carry a "last checked" date.** Re-check against the linked pages before changing it.
- **Screenshots:** `docs/marketing-screens.md`.
- **Lighthouse:** `node scripts/lighthouse.cjs` in `frontend/` against a running production image.
