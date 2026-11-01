import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { CACHE_FILE, CACHE_PAGE, MARKETING_PAGES, SESSION_COOKIE } from "../lib/marketing/routes.mjs";

/**
 * Who may keep each response, read from the real server. This is what stands between one visitor's
 * answer and the next visitor's screen once Cloudflare caches the site (docs/seo.md), so every route
 * is listed from the files themselves: a new app page is checked without anyone remembering to.
 */

const APP_DIR = path.join(__dirname, "..", "app", "(app)");

function appRoutes(dir = APP_DIR, prefix = ""): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    if (entry.isDirectory()) return appRoutes(path.join(dir, entry.name), `${prefix}/${entry.name.replace(/^\[.*\]$/, "00000000-0000-4000-8000-000000000000")}`);
    return entry.name === "page.tsx" ? [prefix || "/"] : [];
  });
}

const PRIVATE = "private, no-store";
const ALL = [...MARKETING_PAGES];
const APP = appRoutes();

test.describe.configure({ mode: "serial" });
test.skip(({ viewport }) => (viewport?.width ?? 0) !== 1366, "headers do not depend on the window; one run is enough");

for (const page of ALL) {
  test(`${page}: public, no cookie set`, async ({ request }) => {
    const response = await request.get(page, { maxRedirects: 0 });
    expect(response.status()).toBe(200);
    expect(response.headers()["cache-control"]).toBe(CACHE_PAGE);
    expect(response.headers()["set-cookie"]).toBeUndefined();
  });

  test(`${page}: private for anyone with a session cookie, and for router requests`, async ({ request }) => {
    const withCookie = await request.get(page, { headers: { Cookie: `${SESSION_COOKIE}=not-a-real-session` }, maxRedirects: 0 });
    expect(withCookie.headers()["cache-control"]).toMatch(/^private/);
    // The router's payload on the page's own address must never be what the edge keeps.
    const router = await request.get(page, { headers: { RSC: "1" }, maxRedirects: 0 });
    expect(router.headers()["cache-control"]).toMatch(/^private/);
    const prefetch = await request.get(`${page}?_rsc=abc12`, { maxRedirects: 0 });
    expect(prefetch.headers()["cache-control"]).toMatch(/^private/);
  });
}

for (const route of APP) {
  test(`${route}: an app page is private`, async ({ request }) => {
    const response = await request.get(route, { maxRedirects: 0 });
    // Same address as before, and never moved by this policy.
    expect(response.status(), route).toBeLessThan(500);
    expect(response.headers()["cache-control"], route).toBe(PRIVATE);
    const router = await request.get(route, { headers: { RSC: "1" }, maxRedirects: 0 });
    expect(router.headers()["cache-control"], `${route} (router)`).toBe(PRIVATE);
  });
}

test("an address nobody listed counts as private", async ({ request }) => {
  for (const route of ["/no-such-page", "/features/no-such-feature", "/guides/no-such-guide", "/compare/no-such-page", "/og/no-such-card.png", "/dashboard/nothing/here"]) {
    const response = await request.get(route, { maxRedirects: 0 });
    expect(response.status(), route).toBe(404);
    expect(response.headers()["cache-control"], route).toMatch(/^private/);
    expect(response.headers()["cache-control"], route).not.toMatch(/public|s-maxage/);
  }
});

test("the home page: a live or dead session is never cacheable, and the page itself asks nothing", async ({ request }) => {
  const anonymous = await request.get("/", { maxRedirects: 0 });
  expect(anonymous.status()).toBe(200);
  expect(anonymous.headers()["cache-control"]).toBe(CACHE_PAGE);
  expect(await anonymous.text()).not.toMatch(/example\.test/);
  // A cookie the API does not know: the landing page, the dead cookie cleared, nothing kept.
  const dead = await request.get("/", { headers: { Cookie: `${SESSION_COOKIE}=not-a-real-session` }, maxRedirects: 0 });
  expect(dead.status()).toBe(200);
  expect(dead.headers()["cache-control"]).toMatch(/^private/);
});

test("files: pictures, the share cards, robots and the sitemap may be kept", async ({ request }) => {
  for (const route of ["/screens/site-review.webp", "/og/home.png", "/og/guides-etsy-title-rules-2026.png", "/robots.txt", "/sitemap.xml", "/icon.svg"]) {
    const response = await request.get(route);
    expect(response.status(), route).toBe(200);
    expect(response.headers()["cache-control"], route).toBe(CACHE_FILE);
    expect(response.headers()["set-cookie"], route).toBeUndefined();
  }
  const home = await request.get("/");
  const chunk = (await home.text()).match(/\/_next\/static\/[^"']+\.js/)![0];
  const asset = await request.get(chunk);
  expect(asset.status()).toBe(200);
  expect(asset.headers()["cache-control"]).toMatch(/public, max-age=31536000, immutable/);
});

test("the API: private, whatever the answer", async ({ request }) => {
  for (const route of ["/api/meta", "/api/account/session", "/api/batches", "/api/no-such-thing"]) {
    const response = await request.get(route, { maxRedirects: 0 });
    expect(response.headers()["cache-control"], route).toBe(PRIVATE);
    expect(response.headers()["cache-control"], route).not.toMatch(/public|s-maxage/);
  }
});
