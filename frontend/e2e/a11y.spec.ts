import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { signIn } from "./session";

/**
 * Every app page passes axe's WCAG 2.x A and AA rules (app/onboarding-v1), signed in as the synthetic
 * demo admin (seed-demo --admin; never a real account), with its data on screen: Home, every rail
 * entry and its tabs, a batch, its review screen, and each Admin tab. A violation names the rule,
 * how many elements and the first one, so it can be found.
 *
 * Runs with the layout tests (playwright.config.ts): LAYOUT_EMAIL and LAYOUT_PASSWORD, else skipped.
 */
const EMAIL = process.env.LAYOUT_EMAIL;
const PASSWORD = process.env.LAYOUT_PASSWORD;

const PAGES = ["/dashboard", "/batches", "/upload", "/templates/designs", "/review", "/scheduled", "/season-planner", "/analytics",
  "/listing-health", "/connect", "/settings", "/profiles", "/templates", "/admin"];

async function audit(page: Page, where: string): Promise<string[]> {
  await page.waitForLoadState("networkidle").catch(() => undefined);
  await page.waitForTimeout(600);
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  return result.violations.map((v) => `${where}: ${v.id} (${v.impact}) ×${v.nodes.length}: ${v.nodes[0]?.target.join(" ")} — ${v.help}`);
}

test.describe("accessibility: every app page", () => {
  test.beforeEach(({}, info) => {
    test.skip(!EMAIL || !PASSWORD, "LAYOUT_EMAIL and LAYOUT_PASSWORD (the synthetic demo admin) are not set");
    test.skip(info.project.name === "tablet-768", "phone and desktop are enough for axe");
  });

  test("axe finds no WCAG A or AA violation", async ({ page }) => {
    test.setTimeout(300_000);
    await signIn(page, EMAIL!, PASSWORD!);
    const found: string[] = [];
    for (const path of PAGES) {
      await page.goto(path);
      found.push(...(await audit(page, path)));
    }
    // A batch and its review screen, from the demo data.
    await page.goto("/batches");
    const href = await page.locator('a[href^="/batches/"]').first().getAttribute("href");
    const batch = href!.replace(/\/review$/, "");
    await page.goto(batch);
    found.push(...(await audit(page, "batch")));
    await page.goto(`${batch}/review`);
    found.push(...(await audit(page, "review")));
    // Each Admin tab.
    await page.goto("/admin");
    for (const tab of ["Invites", "Usage", "Funnel"]) {
      await page.getByRole("tab", { name: new RegExp(`^${tab}`) }).click();
      found.push(...(await audit(page, `admin ${tab}`)));
    }
    expect(found, found.join("\n")).toEqual([]);
  });
});
