import { expect, test, type Page } from "@playwright/test";
import { TRADEMARK_NOTICE } from "../lib/legal";
import { jsonLdNames } from "../lib/marketing/brandRule";
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
      twitterTitle: document.querySelector('meta[name="twitter:title"]')?.getAttribute("content") ?? "",
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
    expect(r.twitterTitle).toBe(r.title);
    for (const text of [r.title, r.ogTitle, r.twitterTitle, ...r.h1, ...r.jsonLd.flatMap((block) => jsonLdNames(JSON.parse(block)))]) {
      expect(text, `${path}: "Etsy" in a title, h1 or JSON-LD name`).not.toMatch(/etsy/i);
    }
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

test("the notice is exactly the required text, in the footer of the home page as of every page", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("trademark-notice")).toHaveText(TRADEMARK_NOTICE);
  await expect(page.getByTestId("home-trademark-notice")).toHaveCount(0);
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

test("at most five top navigation items; on a phone the header keeps the logo, Sign in and Start free", async ({ page }, info) => {
  await page.goto("/");
  const r = await rendered(page);
  expect(r.topNav).toBeLessThanOrEqual(5);
  // The reference hides the menu below 1000 px and has no menu button: every page is in the footer.
  await expect(page.getByTestId("header-cta")).toBeVisible();
  await expect(page.getByRole("link", { name: "Sign in" })).toBeVisible();
  for (const name of ["Features", "Guides", "Compare", "Help"]) await expect(page.getByRole("contentinfo").getByRole("link", { name, exact: true })).toBeVisible();
  if (info.project.name === "phone-375") await expect(page.getByTestId("top-nav")).toBeHidden();
});

test("the home page: the reference's sections, real mockups, three testimonials, prices from the plans", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("hero-demo")).toBeVisible();
  await expect(page.getByTestId("hero-mockups").locator("img")).toHaveCount(8);
  await expect(page.getByTestId("hero-listing").locator(".hd-pop")).toHaveCount(13);
  await expect(page.getByTestId("hero-shops").locator(".hd-in")).toHaveCount(3);
  await expect(page.getByTestId("step-colours").locator("img")).toHaveCount(6);
  await expect(page.getByTestId("shop-cards").locator("img")).toHaveCount(3);
  for (const img of await page.locator("main img").all()) await expect(img).toHaveAttribute("alt", /.{12}/);
  // Every picture is AVIF first, WebP as the fallback, with its own size.
  await expect(page.locator('main picture source[type="image/avif"]')).toHaveCount(17);
  await expect(page.getByTestId("testimonial")).toHaveCount(3);
  await expect(page.getByTestId("promise")).toHaveCount(0);
  await expect(page.getByTestId("hero-video")).toHaveCount(0);
  await expect(page.getByTestId("hero-cta")).toHaveText("Start free with 10 listings");
  await expect(page.getByTestId("closing-cta")).toHaveText("Start free");
  await expect(page.getByTestId("plan")).toHaveCount(3);
  await expect(page.getByTestId("plan-highlight")).toContainText("Growth");
  await expect(page.getByTestId("plan-per-listing").first()).toHaveText("12.7\u00a2 a listing");
  await expect(page.getByRole("heading", { name: "About 10 cents a listing." })).toBeVisible();
  await expect(page.getByTestId("planned-notice")).toContainText("Nothing is charged yet");
});

/** Each demonstration piece's look and its place in the layout: [opacity, left, top, width, height]. The place is
 *  the layout box (offsetLeft/offsetTop up the offset parents), which a transform does not move: the animation
 *  slides and scales pieces, but where each one sits never changes. */
async function demoState(page: Page) {
  return page.evaluate(() => [...document.querySelectorAll('[data-testid="hero-demo"] .hd-in, [data-testid="hero-demo"] .hd-pop')].map((el) => {
    let x = 0;
    let y = 0;
    for (let node = el as HTMLElement | null; node; node = node.offsetParent as HTMLElement | null) { x += node.offsetLeft; y += node.offsetTop; }
    const box = el as HTMLElement;
    return [Number(getComputedStyle(el).opacity), x, y, box.offsetWidth, box.offsetHeight];
  }));
}

test("the hero with reduced motion: the final state at once, nothing animating", async ({ browser }) => {
  const context = await browser.newContext({ reducedMotion: "reduce" });
  const page = await context.newPage();
  await page.goto("/");
  const first = await demoState(page);
  expect(first.length).toBeGreaterThan(20);
  expect(first.every(([opacity]) => opacity === 1), "every piece is shown").toBe(true);
  const running = await page.evaluate(() => document.getAnimations().filter((a) => (a as CSSAnimation).animationName?.startsWith("hd-")).length);
  expect(running).toBe(0);
  await context.close();
});

test("the hero with motion: plays once, rests on the final state, and never moves the page", async ({ browser }) => {
  const context = await browser.newContext({ reducedMotion: "no-preference" });
  const page = await context.newPage();
  await page.addInitScript(() => {
    (window as unknown as { shifts: number }).shifts = 0;
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries() as unknown as { value: number; hadRecentInput: boolean }[]) if (!entry.hadRecentInput) (window as unknown as { shifts: number }).shifts += entry.value;
    }).observe({ type: "layout-shift", buffered: true });
  });
  await page.goto("/");
  const early = await demoState(page);
  await page.waitForTimeout(5000);
  const late = await demoState(page);
  // Every piece holds the place it ends in from the first frame.
  expect(late.map(([, ...box]) => box)).toEqual(early.map(([, ...box]) => box));
  expect(late.every(([opacity]) => opacity === 1), "the final state: all shown").toBe(true);
  const left = await page.evaluate(() => document.getAnimations().filter((a) => (a as CSSAnimation).animationName?.startsWith("hd-") && a.playState === "running").length);
  expect(left, "nothing still playing after five seconds").toBe(0);
  expect(await page.evaluate(() => (window as unknown as { shifts: number }).shifts)).toBeLessThan(0.01);
  await context.close();
});

test("the help center: search hides the answers that do not match, and every answer is in the page", async ({ page }) => {
  await page.goto("/help");
  const all = await page.locator("[data-help-item]").count();
  expect(all).toBeGreaterThan(10);
  await page.getByTestId("help-search").fill("trademark filter");
  await expect(page.getByTestId("help-count")).toHaveText(/1 answer matches|\d+ answers match/);
  expect(await page.locator("[data-help-item]:visible").count()).toBeLessThan(all);
  await expect(page.locator("[data-help-item]:visible").first()).toContainText(/trademark/i);
  await page.getByTestId("help-search").fill("zzzz no such word");
  await expect(page.getByTestId("help-count")).toContainText("No answer matches");
  await page.getByTestId("help-search").fill("");
  expect(await page.locator("[data-help-item]:visible").count()).toBe(all);
});

test("pricing: the switch shows yearly prices and per-listing costs from the plans, and the highlighted plan is Growth", async ({ page }) => {
  await page.goto("/pricing");
  await expect(page.getByTestId("planned-notice")).toContainText("Nothing is charged yet");
  await expect(page.getByTestId("plan-price").first()).toHaveText("$19");
  await page.getByTestId("billing-yearly").click();
  await expect(page.getByTestId("plan-price").first()).toHaveText("$190");
  await expect(page.getByTestId("plan-per-listing").first()).toHaveText("10.6\u00a2 a listing");
  await expect(page.getByTestId("trial-line")).toContainText("10 free AI listings once you connect an Etsy shop");
  await expect(page.getByTestId("plan-highlight")).toBeVisible();
  await expect(page.getByTestId("extra-pack")).toContainText("+100 AI listings for $9");
});

test("a page that does not exist helps, says 404 and stays out of search", async ({ page }, info) => {
  const response = await page.goto("/no-such-page");
  expect(response!.status()).toBe(404);
  await expect(page.getByRole("heading", { level: 1 })).toContainText("can't find that page");
  await expect(page.getByRole("link", { name: "Help center" }).first()).toBeVisible();
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute("content", /noindex/); // exactly one: Next adds it to a 404
  if (SHOTS && info.project.name !== "tablet-768") await picture(page, `${SHOTS}/not-found-${info.project.name}.png`);
});

test("a guide: the holiday dates lead, and its unread Etsy pages are kept apart from its sources", async ({ page }) => {
  await page.goto("/guides/holiday-listing-calendar-pod");
  await expect(page.locator("article table").first().locator("tbody tr").first()).toContainText("26 Nov 2026");
  await expect(page.locator("article table").first()).not.toContainText("31 Oct 2026");
  await expect(page.getByText("This season, already passed: Halloween (31 Oct 2026)")).toBeVisible();
  await expect(page.getByTestId("also-read")).toContainText("Nothing above relies on them");
  await page.goto("/guides/running-more-than-one-etsy-shop");
  await expect(page.locator("article")).toContainText(`"for the purpose of shilling, manipulating search or circumventing Etsy's policies"`);
  await page.locator('article a[href="/features/multi-shop-publishing#per-shop-text"]').click();
  await expect(page.locator("#per-shop-text")).toBeVisible();
});

test("robots and the sitemap say what they should", async ({ request }) => {
  const robots = await (await request.get("/robots.txt")).text();
  for (const line of ["Disallow: /api/", "Disallow: /dashboard", "Disallow: /batches", "Disallow: /admin", "Sitemap:"]) expect(robots).toContain(line);
  const sitemap = await (await request.get("/sitemap.xml")).text();
  for (const path of MARKETING_PAGES) if (path !== "/") expect(sitemap, path).toContain(`${path}</loc>`);
  for (const path of ["/dashboard", "/batches", "/login", "/api"]) expect(sitemap, path).not.toContain(`${path}</loc>`);
});
