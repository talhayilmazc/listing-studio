import { expect, test, type Page } from "@playwright/test";
import { TRADEMARK_NOTICE } from "../lib/legal";
import { MARKETING_PAGES } from "../lib/marketing/routes.mjs";

/** Every public page, rendered: what a search engine and a visitor actually get. */

const SHOTS = process.env.SITE_SHOTS ? "../load-output/site-shots" : null;

async function rendered(page: Page) {
  return page.evaluate((trademark) => {
    const flat = (t: string | null) => (t ?? "").replace(/\s+/g, " ").trim();
    return {
      h1: [...document.querySelectorAll("h1")].map((el) => flat(el.textContent)),
      title: document.title,
      description: document.querySelector('meta[name="description"]')?.getAttribute("content") ?? "",
      canonical: document.querySelector('link[rel="canonical"]')?.getAttribute("href") ?? "",
      ogImage: document.querySelector('meta[property="og:image"]')?.getAttribute("content") ?? "",
      ogTitle: document.querySelector('meta[property="og:title"]')?.getAttribute("content") ?? "",
      noAlt: [...document.querySelectorAll("img")].filter((img) => !img.getAttribute("alt")).map((img) => img.getAttribute("src")),
      unsized: [...document.querySelectorAll("img")].filter((img) => !img.getAttribute("width") || !img.getAttribute("height")).map((img) => img.getAttribute("src")),
      jsonLd: [...document.querySelectorAll('script[type="application/ld+json"]')].map((el) => el.textContent ?? ""),
      notice: flat(document.body.innerText).split(trademark).length - 1,
      links: [...new Set([...document.querySelectorAll("a[href]")].map((a) => a.getAttribute("href")!))],
      topNav: [...document.querySelectorAll('[data-testid="top-nav"] a')].length,
      wide: document.documentElement.scrollWidth - window.innerWidth,
    };
  }, TRADEMARK_NOTICE);
}

/** A whole page, once every image has really been drawn: a lazy image can be fetched and still blank. */
async function picture(page: Page, file: string) {
  await page.evaluate(async () => {
    const images = [...document.querySelectorAll("img")];
    for (const img of images) { img.loading = "eager"; img.decoding = "sync"; }
    await Promise.all(images.map((img) => img.decode().catch(() => undefined)));
  });
  await page.waitForLoadState("networkidle");
  await page.screenshot({ path: file, fullPage: true });
}

for (const path of MARKETING_PAGES) {
  test(`${path}: one h1, its own metadata, valid JSON-LD, alt text, the notice, no sideways scroll`, async ({ page }, info) => {
    const response = await page.goto(path);
    expect(response!.status()).toBe(200);
    await page.waitForLoadState("load");
    const r = await rendered(page);
    expect(r.h1, `${path} has exactly one h1`).toHaveLength(1);
    expect(r.h1[0].length).toBeGreaterThan(3);
    expect(r.title).toContain("Listyro");
    expect(r.description.length).toBeGreaterThan(40);
    expect(new URL(r.canonical, "https://listyro.com").pathname).toBe(path);
    expect(r.ogImage).toMatch(/\/og\/[a-z0-9-]+\.png$/);
    expect(r.ogTitle).toBe(r.title);
    expect(r.noAlt, `${path}: images without alt text`).toEqual([]);
    expect(r.unsized, `${path}: images with no size move the page`).toEqual([]);
    expect(r.jsonLd.length, `${path}: structured data`).toBeGreaterThan(0);
    for (const block of r.jsonLd) expect(() => JSON.parse(block), block.slice(0, 80)).not.toThrow();
    expect(r.jsonLd.join(" ")).not.toMatch(/aggregateRating|ratingValue/);
    if (path !== "/") expect(r.jsonLd.join(" ")).toContain("BreadcrumbList");
    expect(r.notice, `${path}: the trademark notice`).toBeGreaterThanOrEqual(1);
    expect(r.wide, `${path} scrolls sideways`).toBeLessThanOrEqual(0);
    if (SHOTS && info.project.name !== "tablet-768") {
      const name = path === "/" ? "home" : path.slice(1).replace(/\//g, "-");
      await picture(page, `${SHOTS}/${name}-${info.project.name}.png`);
    }
  });
}

test("the notice is exactly the required text, on the home page and in every footer", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("home-trademark-notice")).toHaveText(TRADEMARK_NOTICE);
  await expect(page.getByTestId("trademark-notice")).toHaveText(TRADEMARK_NOTICE);
});

test("every internal link on every public page leads to a page that answers", async ({ page, request }, info) => {
  test.skip(info.project.name !== "desktop-1366", "links do not depend on the window");
  test.setTimeout(240_000);
  const seen = new Map<string, number>();
  for (const path of MARKETING_PAGES) {
    await page.goto(path);
    const { links } = await rendered(page);
    for (const href of links) {
      if (!href.startsWith("/")) continue;
      const clean = href.split("#")[0].split("?")[0];
      if (clean && !seen.has(clean)) seen.set(clean, (await request.get(clean, { maxRedirects: 0 })).status());
    }
  }
  const broken = [...seen].filter(([, status]) => status >= 400);
  expect(broken).toEqual([]);
  expect(seen.size).toBeGreaterThan(MARKETING_PAGES.length - 3);
});

test("at most five top navigation items, and a menu that opens on a phone", async ({ page }, info) => {
  await page.goto("/");
  const r = await rendered(page);
  expect(r.topNav).toBeLessThanOrEqual(5);
  if (info.project.name === "phone-375") {
    await page.getByTestId("site-menu").locator("summary").click();
    await expect(page.getByTestId("site-menu").getByRole("link", { name: "Guides" })).toBeVisible();
  }
});

test("the home page: four steps with real screenshots, three differentiators, the pricing teaser, no testimonials", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("how-step")).toHaveCount(4);
  for (const img of await page.getByTestId("how-step").locator("img").all()) await expect(img).toHaveAttribute("alt", /.{20}/);
  await expect(page.getByTestId("differentiator")).toHaveCount(3);
  await expect(page.getByTestId("demo-video-slot")).toBeVisible();
  await expect(page.getByTestId("home-plans").locator("li")).toHaveCount(4);
  await expect(page.getByTestId("home-planned-notice")).toContainText("No payment is taken today");
  await expect(page.getByTestId("testimonials")).toHaveCount(0);
});

test("a page that does not exist helps, says 404 and stays out of search", async ({ page }, info) => {
  const response = await page.goto("/no-such-page");
  expect(response!.status()).toBe(404);
  await expect(page.getByRole("heading", { level: 1 })).toContainText("can't find that page");
  await expect(page.getByRole("link", { name: "Help center" }).first()).toBeVisible();
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute("content", /noindex/); // exactly one: Next adds it to a 404
  if (SHOTS && info.project.name !== "tablet-768") await picture(page, `${SHOTS}/not-found-${info.project.name}.png`);
});

test("robots and the sitemap say what they should", async ({ request }) => {
  const robots = await (await request.get("/robots.txt")).text();
  for (const line of ["Disallow: /api/", "Disallow: /dashboard", "Disallow: /batches", "Disallow: /admin", "Sitemap:"]) expect(robots).toContain(line);
  const sitemap = await (await request.get("/sitemap.xml")).text();
  for (const path of MARKETING_PAGES) if (path !== "/") expect(sitemap, path).toContain(`${path}</loc>`);
  for (const path of ["/dashboard", "/batches", "/login", "/api"]) expect(sitemap, path).not.toContain(`${path}</loc>`);
});
