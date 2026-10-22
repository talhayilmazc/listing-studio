/**
 * Every page, in every state (quality pass 2026-10, docs/qa-2026-10.md section 7).
 *
 * As the synthetic demo admin (1, 4 or 15 shops, whichever the demo was seeded with), at each viewport:
 *   - no page logs a JavaScript error or a React error, and no internal link on any page is broken;
 *   - when the server fails every request, each page says so in words (never a blank page or a crash);
 *   - on a slow connection each page shows that it is loading, then its content;
 *   - with nothing yet (no batches), the page says what to do.
 *
 * Network faults are made in the browser (page.route), against our own server only.
 * Runs with the layout test: playwright.config.ts. Skipped without LAYOUT_EMAIL / LAYOUT_PASSWORD.
 */
import { expect, test, type Page } from "@playwright/test";

const EMAIL = process.env.LAYOUT_EMAIL;
const PASSWORD = process.env.LAYOUT_PASSWORD;
const PAGES = ["/dashboard", "/batches", "/upload", "/profiles", "/templates", "/templates/designs",
  "/scheduled", "/analytics", "/connect", "/settings", "/admin"];
/** What the app needs to draw its frame: these are never failed or slowed. */
const FRAME = /\/api\/(account\/(me|session)|meta)(\?|$)/;

async function signIn(page: Page): Promise<void> {
  await page.goto("/login");
  await page.locator("#email").fill(EMAIL!);
  await page.locator("#password").fill(PASSWORD!);
  await page.locator('button[type="submit"]').click();
  await page.waitForURL(/\/dashboard/, { timeout: 30_000 });
}

/** JavaScript errors and React's own error messages while the page is open. */
function watchErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(`${page.url()}: ${e.message}`));
  page.on("console", (m) => {
    // A failed request is logged by the browser as an error too; those are checked below by status.
    if (m.type() === "error" && !/Failed to load resource/.test(m.text())) errors.push(`${page.url()}: ${m.text()}`);
  });
  return errors;
}

async function quiet(page: Page): Promise<void> {
  await page.waitForLoadState("load");
  await page.waitForTimeout(1200);
}

test.describe("every page, every state", () => {
  test.skip(!EMAIL || !PASSWORD, "set LAYOUT_EMAIL and LAYOUT_PASSWORD (the demo admin from `seed-demo --admin`)");
  test.beforeEach(async ({ page }) => signIn(page));

  test("no JavaScript errors and no broken internal links on any page", async ({ page }) => {
    const errors = watchErrors(page);
    const pages = [...PAGES];
    await page.goto("/batches");
    await quiet(page);
    const batch = await page.locator('a[href^="/batches/"]').first().getAttribute("href").catch(() => null);
    if (batch) pages.push(batch.replace(/\/review$/, ""), `${batch.replace(/\/review$/, "")}/review`);
    const links = new Set<string>();
    const failed: string[] = [];
    page.on("response", (r) => {
      const url = new URL(r.url());
      if (url.pathname.startsWith("/api/") && r.status() >= 500) failed.push(`${r.status()} ${url.pathname}`);
    });
    for (const path of pages) {
      await page.goto(path);
      await quiet(page);
      for (const href of await page.locator('a[href^="/"]').evaluateAll((els) => els.map((e) => e.getAttribute("href") ?? ""))) {
        if (!href.startsWith("/api/") && !href.includes("#")) links.add(href.split("?")[0]);
      }
    }
    const broken: string[] = [];
    for (const href of links) {
      const res = await page.request.get(href);
      if (res.status() >= 400) broken.push(`${res.status()} ${href}`);
    }
    expect(errors, "JavaScript errors").toEqual([]);
    expect(failed, "server errors while the pages loaded").toEqual([]);
    expect(broken, "broken internal links").toEqual([]);
  });

  test("when every request fails, each page says so in words", async ({ page }) => {
    const errors = watchErrors(page);
    await page.route("**/api/**", (route) =>
      FRAME.test(route.request().url())
        ? route.continue()
        : route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "internal error", request_id: "e2e-test" }) }));
    const silent: string[] = [];
    for (const path of PAGES) {
      await page.goto(path);
      await quiet(page);
      const said = await page.getByText(/try again|could not|couldn.t|did not work|not reach|no longer/i).count();
      const main = (await page.locator("main").innerText().catch(() => "")).trim();
      if (!said) silent.push(`${path}: "${main.slice(0, 80).replace(/\s+/g, " ")}"`);
    }
    expect(errors, "a failed request must never crash a page").toEqual([]);
    expect(silent, "pages that do not say their data could not be loaded").toEqual([]);
  });

  test("on a slow connection each page shows it is loading, then its content", async ({ page }) => {
    const errors = watchErrors(page);
    await page.route("**/api/**", async (route) => {
      if (!FRAME.test(route.request().url())) await new Promise((r) => setTimeout(r, 2500));
      await route.continue();
    });
    const blank: string[] = [];
    for (const path of ["/dashboard", "/batches", "/scheduled", "/profiles"]) {
      await page.goto(path, { waitUntil: "domcontentloaded" });
      await page.waitForTimeout(800);
      const loading = await page.locator('[aria-busy="true"], [data-testid*="skeleton"], .animate-pulse').count()
        + await page.getByText(/loading|reading/i).count();
      if (!loading) blank.push(path);
      await quiet(page);
      await page.waitForTimeout(3000);
    }
    expect(errors).toEqual([]);
    expect(blank, "pages that show nothing while they load").toEqual([]);
  });

  test("with nothing yet, the page says what to do", async ({ page }) => {
    await page.route(/\/api\/batches(\?|$)/, (route) => route.fulfill({ status: 200, contentType: "application/json", body: "[]" }));
    await page.goto("/batches");
    await quiet(page);
    await expect(page.getByText(/no batches|upload/i).first()).toBeVisible();
  });
});
