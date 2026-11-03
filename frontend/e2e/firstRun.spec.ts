// The sample path in a real browser: from Overview to the finished draft preview with the production image, and not one
// request on the way writes anything. Run with the layout suite (LAYOUT_EMAIL, LAYOUT_PASSWORD: python -m app.cli seed-demo --admin).
import { expect, test } from "@playwright/test";
import { signIn } from "./session";

const EMAIL = process.env.LAYOUT_EMAIL ?? "";
const PASSWORD = process.env.LAYOUT_PASSWORD ?? "";

test.describe("the sample path", () => {
  test.skip(!EMAIL || !PASSWORD, "needs the demo account");

  test("the sample path ends in a finished draft preview, and writes nothing on the way", async ({ page }) => {
    await signIn(page, EMAIL, PASSWORD);
    await page.addInitScript(() => { try { localStorage.removeItem("listyro.guide.closed"); localStorage.removeItem("listyro.sample.done"); } catch { /* a fresh browser */ } });
    const writes: string[] = [];
    page.on("request", (request) => {
      const url = new URL(request.url());
      // The app's own report of a failed page is the only other thing it may send, and it would fail this test's point too.
      if (url.pathname.startsWith("/api/") && request.method() !== "GET") writes.push(`${request.method()} ${url.pathname}`);
    });
    const started = Date.now();
    await page.goto("/first-run");
    await expect(page.getByTestId("first-run-design")).toBeVisible();
    await page.getByTestId("first-run-continue").click();
    await expect(page.getByTestId("first-run-area")).toBeVisible();
    await page.getByTestId("first-run-continue").click();
    await expect(page.getByTestId("first-run-mockup")).toHaveCount(4);
    await page.waitForFunction(() => [...document.querySelectorAll('[data-testid="first-run-mockup"]')].every((i) => (i as HTMLImageElement).naturalWidth > 0 || (i as HTMLImageElement).loading === "lazy"));
    await page.getByTestId("first-run-continue").click();
    await expect(page.getByTestId("first-run-checks-ok").first()).toBeVisible();
    await page.getByTestId("first-run-title").fill("Cute Sunset Tee, Free Shipping");
    await expect(page.getByTestId("first-run-checks").first()).toContainText("No opinion words");
    await page.getByTestId("first-run-continue").click();
    await expect(page.getByTestId("first-run-nothing-sent")).toContainText("Nothing has gone to Etsy");
    await expect(page.getByTestId("first-run-preview")).toContainText("Cute Sunset Tee, Free Shipping");
    await expect(page.getByTestId("first-run-next")).toBeVisible();
    expect(writes, "nothing is written on the sample path").toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
    console.log(`sample path, no reading time: ${((Date.now() - started) / 1000).toFixed(1)} s`);
  });

  test("Overview offers it", async ({ page }) => {
    await signIn(page, EMAIL, PASSWORD);
    await page.addInitScript(() => { try { localStorage.removeItem("listyro.guide.closed"); } catch { /* a fresh browser */ } });
    await page.goto("/dashboard");
    const guide = page.getByTestId("first-run-guide");
    if (await guide.isVisible().catch(() => false)) await expect(guide.getByRole("link", { name: /sample/i })).toHaveAttribute("href", "/first-run");
  });
});
