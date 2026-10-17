/**
 * The page ends where its footer ends, nothing is wider than the screen, and the
 * Admin tab bar stays where it is.
 *
 * The print-area editor (components/PrintAreaEditor.tsx) is opened on /templates and must fit
 * the screen with 44 px controls and its confirm button in view.
 *
 * Three faults these tests were written after:
 *   - the sign-in screens kept their footer inside a centred column, so the height
 *     the window had to spare was left below it;
 *   - a line on the Admin Users tab was forbidden to wrap and ran past the edge of
 *     the screen, so the whole page scrolled sideways;
 *   - the Admin tab bar moved while the page loaded: the summary above it grew, and
 *     each tab grew when its count arrived.
 *
 * How to run them: playwright.config.ts. Without LAYOUT_EMAIL and LAYOUT_PASSWORD
 * (the synthetic demo admin) the signed-in tests are skipped, not failed.
 */
import { expect, test, type Page } from "@playwright/test";

const PUBLIC = ["/", "/pricing", "/terms", "/privacy", "/request-invite", "/contact"];
const SIGN_IN = ["/login", "/register"];
const SIGNED_IN = ["/dashboard", "/batches", "/upload", "/profiles", "/templates", "/templates/designs", "/scheduled", "/analytics", "/connect", "/settings"];
const ADMIN_TABS = ["Users", "Invites", "Usage"];

const EMAIL = process.env.LAYOUT_EMAIL;
const PASSWORD = process.env.LAYOUT_PASSWORD;

type Measure = {
  scrollHeight: number;
  /** The bottom edge of the footer, or of the last thing on a page that has none. */
  end: number;
  hasFooter: boolean;
  sideways: number;
  /** What reaches below the footer, to name it in the failure. */
  below: string[];
  /** What is wider than the screen and not inside a container that scrolls by itself. */
  wide: string[];
};

/** Let the page finish: scroll through it so lazy pictures load, then come back up. */
async function settle(page: Page): Promise<void> {
  await page.waitForLoadState("load");
  await page.evaluate(async () => {
    const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
    for (let y = 0; y < document.documentElement.scrollHeight; y += 800) {
      window.scrollTo(0, y);
      await wait(60);
    }
    window.scrollTo(0, 0);
    await wait(250);
  });
  // Skeletons are replaced by content; wait until none is left (or give up quietly: the
  // measurements below then say what is wrong).
  await page
    .waitForFunction(
      () =>
        !document.querySelector("[aria-busy=true]") &&
        // A placeholder that is still showing (the home page keeps faded-out ones in place).
        ![...document.querySelectorAll("main .animate-pulse")].some((el) => el.getClientRects().length > 0 && getComputedStyle(el.parentElement!).opacity !== "0"),
      undefined,
      { timeout: 15_000 },
    )
    .catch(() => {});
  await page.waitForTimeout(400);
}

async function measure(page: Page): Promise<Measure> {
  return page.evaluate(() => {
    const root = document.documentElement;
    const name = (el: Element) =>
      el.tagName.toLowerCase() + (el.id ? `#${el.id}` : "") + "." + String((el as HTMLElement).className ?? "").split(/\s+/).slice(0, 5).join(".");
    const bottom = (el: Element) => el.getBoundingClientRect().bottom + window.scrollY;
    const visible = (el: Element) => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden";
    };
    // The footer: the last <footer>, or, where a page has none by that name, the block
    // that carries the trademark notice (every page of ours ends with it).
    const footers = [...document.querySelectorAll("footer")].filter(visible);
    const notices = [...document.querySelectorAll('[data-testid="trademark-notice"]')].filter(visible);
    const footer = footers[footers.length - 1] ?? notices[notices.length - 1]?.parentElement ?? null;
    // No footer: the last visible thing in the page's own flow.
    const flow = [...document.querySelectorAll("body *")].filter((el) => visible(el) && !["fixed", "absolute"].includes(getComputedStyle(el).position));
    const end = footer ? bottom(footer) : Math.max(0, ...flow.map(bottom));
    const below = footer
      ? [...document.querySelectorAll("body *")]
          .filter((el) => visible(el) && !footer.contains(el) && !el.contains(footer) && bottom(el) > end + 2)
          .slice(0, 5)
          .map((el) => `${name(el)} (${getComputedStyle(el).position}, bottom ${Math.round(bottom(el))})`)
      : [];
    const width = root.clientWidth;
    const scrollsItself = (el: Element) => {
      for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
        if (["auto", "scroll", "hidden", "clip"].includes(getComputedStyle(p).overflowX) && p.getBoundingClientRect().right <= width + 1) return true;
      }
      return false;
    };
    const wide: string[] = [];
    const range = document.createRange();
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node && wide.length < 5; node = walker.nextNode()) {
      const parent = node.parentElement;
      if (!node.textContent?.trim() || !parent || !visible(parent) || getComputedStyle(parent).position === "fixed") continue;
      range.selectNodeContents(node);
      const r = range.getBoundingClientRect();
      if (r.width > 0 && r.right > width + 1 && !scrollsItself(parent)) wide.push(`text in ${name(parent)} reaches ${Math.round(r.right)} of ${width}`);
    }
    return {
      scrollHeight: root.scrollHeight,
      end: Math.round(end),
      hasFooter: Boolean(footer),
      sideways: root.scrollWidth - width,
      below,
      wide,
    };
  });
}

/** The two rules every page keeps. */
async function expectSound(page: Page, what: string): Promise<void> {
  const m = await measure(page);
  expect(m.hasFooter, `${what}: the page has no footer with the trademark notice`).toBe(true);
  expect(m.sideways, `${what}: the page scrolls sideways by ${m.sideways}px. ${m.wide.join("; ")}`).toBeLessThanOrEqual(0);
  expect(
    m.scrollHeight - m.end,
    `${what}: ${m.scrollHeight - m.end}px of the page lie below ${m.hasFooter ? "the footer" : "the last element"}. ${m.below.join("; ")}`,
  ).toBeLessThanOrEqual(2);
}

for (const path of [...PUBLIC, ...SIGN_IN]) {
  test(`${path}: ends at its footer and does not scroll sideways`, async ({ page }) => {
    await page.goto(path);
    await settle(page);
    await expectSound(page, path);
    // A tall window must not open a gap either (the sign-in screens did exactly this).
    const { width } = page.viewportSize()!;
    await page.setViewportSize({ width, height: 1500 });
    await page.waitForTimeout(250);
    await expectSound(page, `${path} in a 1500px tall window`);
  });
}

test.describe("signed in", () => {
  test.skip(!EMAIL || !PASSWORD, "set LAYOUT_EMAIL and LAYOUT_PASSWORD (the demo admin from `seed-demo --admin`) to run these");

  test.beforeEach(async ({ page }) => {
    await page.goto("/login");
    await page.locator("#email").fill(EMAIL!);
    await page.locator("#password").fill(PASSWORD!);
    await page.locator('button[type="submit"]').click();
    await page.waitForURL(/\/(dashboard|accept-terms|password)/, { timeout: 30_000 });
    expect(new URL(page.url()).pathname, "the demo account signs straight in").toBe("/dashboard");
  });

  test("every signed-in page ends at its footer and does not scroll sideways", async ({ page }) => {
    const pages = [...SIGNED_IN];
    await page.goto("/batches");
    await settle(page);
    const batch = await page.locator('a[href^="/batches/"]').first().getAttribute("href").catch(() => null);
    if (batch) pages.push(batch, `${batch}/review`);
    for (const path of pages) {
      await page.goto(path);
      await settle(page);
      await expectSound(page, path.replace(/[0-9a-f-]{36}/, "<batch>"));
    }
  });

  test("the print-area editor fits the screen, its controls are 44 px and nothing scrolls sideways", async ({ page }) => {
    await page.goto("/templates");
    await settle(page);
    // Template cards start collapsed: open them, then a photo opens in the viewer.
    const expand = page.getByTestId("templates-expand-all");
    await expand.waitFor({ state: "visible", timeout: 30_000 });
    if (await expand.isEnabled()) await expand.click();
    await expectSound(page, "/templates with every card open");
    const open = page.getByTestId("template-photo-open").first();
    await open.waitFor({ state: "visible", timeout: 30_000 });
    await open.click();
    const viewer = page.getByTestId("photo-viewer");
    await viewer.waitFor({ state: "visible", timeout: 15_000 });
    const viewerSideways = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(viewerSideways, "the page scrolls sideways with the viewer open").toBeLessThanOrEqual(0);
    const smallInViewer = await viewer.locator("button").evaluateAll((buttons) =>
      buttons.filter((b) => b.getClientRects().length).map((b) => ({ t: b.textContent?.trim(), h: b.getBoundingClientRect().height, w: b.getBoundingClientRect().width }))
        .filter((b) => b.h < 44 || b.w < 44));
    expect(smallInViewer, "viewer buttons under 44 px").toEqual([]);
    await page.getByTestId("viewer-check").click();
    const editor = page.getByTestId("area-editor");
    await page.getByTestId("editor-confirm").waitFor({ state: "visible", timeout: 15_000 });
    const { width, height } = page.viewportSize()!;
    const panel = (await editor.locator(":scope > div").boundingBox())!;
    // Full screen on a phone; at least 900 px wide on a desktop, inside the window.
    if (width < 1024) expect(Math.round(panel.width), "the editor is full width on a phone").toBe(width);
    else expect(panel.width, "the editor is at least 900 px wide on a desktop").toBeGreaterThanOrEqual(900);
    expect(panel.x).toBeGreaterThanOrEqual(0);
    expect(panel.x + panel.width).toBeLessThanOrEqual(width + 1);
    expect(panel.y + panel.height).toBeLessThanOrEqual(height + 1);
    const sideways = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(sideways, "the page scrolls sideways with the editor open").toBeLessThanOrEqual(0);
    // Every button in the editor, and the confirm button in view, at least 44 px.
    const small = await editor.locator("button").evaluateAll((buttons) =>
      buttons.filter((b) => b.getClientRects().length).map((b) => ({ t: b.textContent?.trim() || b.getAttribute("aria-label"), h: b.getBoundingClientRect().height, w: b.getBoundingClientRect().width }))
        .filter((b) => b.h < 44 || b.w < 44));
    expect(small, "editor buttons under 44 px").toEqual([]);
    const confirm = (await page.getByTestId("editor-confirm").boundingBox())!;
    expect(confirm.y + confirm.height, "Looks right is on screen without scrolling").toBeLessThanOrEqual(height + 1);
    await page.getByTestId("editor-close").click();
    await expectSound(page, "/templates after the editor");
  });

  test("Admin: no tab scrolls sideways, and the tab bar does not move", async ({ page }) => {
    await page.goto("/admin", { waitUntil: "commit" });
    const bar = page.getByRole("tablist", { name: "Admin sections" });
    await bar.waitFor({ state: "visible", timeout: 30_000 });
    const boxes = async () =>
      page.evaluate(() => {
        const list = document.querySelector('[role="tablist"]')!;
        const box = (el: Element) => {
          const r = el.getBoundingClientRect();
          return { x: Math.round(r.left), y: Math.round(r.top + window.scrollY), w: Math.round(r.width), h: Math.round(r.height) };
        };
        return { bar: box(list), tabs: [...list.querySelectorAll('[role="tab"]')].map(box), page: document.documentElement.clientWidth };
      });

    // While the page loads: from the first paint of the tab bar to the last figure arriving.
    const first = await boxes();
    await settle(page);
    const loaded = await boxes();
    expect(loaded, "the tab bar moved or a tab changed size while the page was loading").toEqual(first);
    await expect(page.getByTestId("pending-requests-badge"), "the demo data has invite requests waiting").toBeVisible();

    // Switching tabs: the bar and every tab stay exactly where they were.
    for (const tab of [...ADMIN_TABS, ADMIN_TABS[0]]) {
      await page.getByRole("tab", { name: new RegExp(`^${tab}`) }).click();
      expect(await boxes(), `the tab bar moved at the moment ${tab} was opened`).toEqual(loaded);
      await settle(page);
      expect(await boxes(), `the tab bar moved after ${tab} had loaded`).toEqual(loaded);
      await expectSound(page, `Admin, ${tab}`);
    }

    // The badge's slot is there whether or not anything is waiting.
    const slot = page.getByTestId("pending-requests-slot");
    const width = (await slot.boundingBox())!.width;
    await page.evaluate(() => document.querySelector('[data-testid="pending-requests-badge"]')?.remove());
    expect((await slot.boundingBox())!.width, "the slot of the pending-requests badge").toBe(width);
    expect(await boxes()).toEqual(loaded);
  });
});
