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
 * Text reads: the sidebar's entries and headings, the body's paragraphs, labels and tables, and
 * every enabled primary button are at least 4.5:1 against their own background (lowContrast).
 *
 * The trademark notice is in its places (the box under the home page's call to action, above the fold
 * at 375 and 1366; every footer; beside "Connect a shop"; the Terms) and once in each, never twice.
 *
 * How to run them: playwright.config.ts. Without LAYOUT_EMAIL and LAYOUT_PASSWORD
 * (the synthetic demo admin) the signed-in tests are skipped, not failed.
 */
import { expect, test, type Page } from "@playwright/test";
import { TRADEMARK_NOTICE } from "../lib/legal";

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

/** The API Terms of Use statement, word for word (one copy: lib/legal.ts). */
const NOTICE = TRADEMARK_NOTICE;

/** Where the notice shows on the page: each element showing it, and how often the page's text has it. */
async function notices(page: Page): Promise<{ places: string[]; inText: number; each: number[] }> {
  return page.evaluate((notice) => {
    const flat = (t: string) => t.replace(/\s+/g, " ").trim();
    const shown = [...document.querySelectorAll("p, div, span, li")].filter((el) => flat(el.textContent ?? "").includes(notice)
      && ![...el.children].some((c) => flat(c.textContent ?? "").includes(notice)));
    const text = flat(document.body.innerText);
    return {
      places: shown.map((el) => el.closest("footer") ? "footer" : (el.getAttribute("data-testid") ?? el.tagName.toLowerCase())),
      inText: text.split(notice).length - 1,
      each: shown.map((el) => flat(el.textContent ?? "").split(notice).length - 1),
    };
  }, NOTICE);
}

for (const path of ["/", "/terms", "/pricing"]) {
  test(`${path}: the trademark notice is in its places, once in each`, async ({ page }) => {
    await page.goto(path);
    await settle(page);
    const found = await notices(page);
    expect(found.each.every((n) => n === 1), `${path}: a place shows the notice more than once`).toBe(true);
    const expected = path === "/" ? ["home-trademark-notice", "footer"] : path === "/terms" ? ["terms-trademark-notice", "footer"] : ["footer"];
    expect(found.places.sort(), `${path}: where the notice is`).toEqual(expected.sort());
    expect(found.inText, `${path}: the notice on the page`).toBe(expected.length);
    if (path === "/") {
      // Above the fold: the whole box in the first screen, at this width.
      const box = await page.getByTestId("home-trademark-notice").boundingBox();
      const { height } = page.viewportSize()!;
      expect(box, "the home page has the notice box").not.toBeNull();
      expect(box!.y + box!.height, "the notice box is above the fold").toBeLessThanOrEqual(height);
      await expect(page.getByTestId("home-trademark-notice")).toHaveText(NOTICE);
    }
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

  test("the trademark notice is beside Connect a shop and in the footer, once in each", async ({ page }) => {
    await page.goto("/connect");
    await settle(page);
    const found = await notices(page);
    expect(found.each.every((n) => n === 1)).toBe(true);
    expect(found.places.sort()).toEqual(["connect-trademark-notice", "footer"].sort());
    expect(found.inText).toBe(2);
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
    // Full width below 1024 px: the whole window on a phone; at 768 px with a desktop scrollbar, the window
    // less the scrollbar's gutter (html keeps it: scrollbar-gutter: stable), at most 17 px.
    if (width < 1024) expect(width - Math.round(panel.width), "the editor is full width on a phone or tablet").toBeLessThanOrEqual(17);
    if (width < 1024) expect(width - Math.round(panel.width)).toBeGreaterThanOrEqual(0);
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

  test("Shops: \"Use all reference listings here\" is laid out and tappable", async ({ page }) => {
    await page.goto("/connect");
    await settle(page);
    const button = page.getByTestId("use-all-here").first();
    await button.waitFor({ state: "visible", timeout: 30_000 });
    await expect(button).toHaveText("Use all reference listings here");
    const line = page.getByTestId("shop-profiles-line").first();
    await expect(line).toContainText("reference listing");
    const { width } = page.viewportSize()!;
    const box = (await button.boundingBox())!;
    if (width < 640) expect(box.height, "44 px on a phone").toBeGreaterThanOrEqual(44);
    expect(box.x + box.width, "inside the screen").toBeLessThanOrEqual(width + 1);
    await expectSound(page, "/connect with the reference listing button");
  });

  test("Use in: the \"All shops\" tick picks every shop, and the setup dialog fits the screen", async ({ page }) => {
    await page.goto("/profiles");
    await settle(page);
    const useIn = page.getByRole("button", { name: "Use in…" }).first();
    await useIn.waitFor({ state: "visible", timeout: 30_000 });
    await useIn.click();
    const all = page.getByTestId("target-all").first();
    await all.waitFor({ state: "visible", timeout: 15_000 });
    await all.check();
    const picker = page.getByTestId("shop-target-picker").first();
    const shops = picker.getByTestId("target-shop");
    const states = await shops.evaluateAll((boxes) => boxes.map((b) => ({ on: (b as HTMLInputElement).checked, off: (b as HTMLInputElement).disabled })));
    expect(states.length, "the picker lists the shops").toBeGreaterThan(1);
    expect(states.filter((s) => !s.off).every((s) => s.on), "All shops ticks every shop that can be chosen").toBe(true);
    await expect(page.getByTestId("use-in-chosen").first()).toContainText(`Set up in ${states.filter((s) => !s.off).length} shops`);
    await expectSound(page, "/profiles with Use in open and All shops ticked");
    const { width, height } = page.viewportSize()!;
    if (width < 640) {
      const tick = (await all.locator("xpath=ancestor::label[1]").boundingBox())!;
      expect(tick.height, "the All shops row is 44 px on a phone").toBeGreaterThanOrEqual(44);
    }

    // Setting up opens the dialog while each shop is read; it fits the screen.
    await page.getByTestId("use-in-chosen").first().click();
    const dialog = page.getByTestId("shop-setup-dialog");
    await dialog.waitFor({ state: "visible", timeout: 15_000 });
    const panel = (await dialog.getByRole("dialog").boundingBox())!;
    expect(panel.x).toBeGreaterThanOrEqual(0);
    expect(panel.x + panel.width, "the dialog is inside the screen").toBeLessThanOrEqual(width + 1);
    expect(panel.y + panel.height, "the dialog is no taller than the screen").toBeLessThanOrEqual(height + 1);
    const sideways = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(sideways, "the page scrolls sideways with the dialog open").toBeLessThanOrEqual(0);
    const wide = await dialog.evaluate((root) => {
      const w = document.documentElement.clientWidth;
      return [...root.querySelectorAll("*")].filter((el) => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.right > w + 1;
      }).slice(0, 5).map((el) => el.tagName.toLowerCase() + "." + String((el as HTMLElement).className).split(" ").slice(0, 3).join("."));
    });
    expect(wide, "nothing in the dialog is wider than the screen").toEqual([]);
    if (width < 640) {
      // The tappable area: the box, or a .tap control's 44 px ::after (it keeps its look, globals.css).
      const small = await dialog.locator("button, select").evaluateAll((els) =>
        els.filter((b) => b.getClientRects().length).map((b) => ({
          t: b.textContent?.trim() || b.getAttribute("aria-label"),
          h: Math.max(b.getBoundingClientRect().height, parseFloat(getComputedStyle(b, "::after").height) || 0),
        })).filter((b) => b.h < 44));
      expect(small, "dialog controls under 44 px on a phone").toEqual([]);
    }
    // The dialog closes by itself once every shop is set up; otherwise it is closed here.
    const close = dialog.getByRole("button", { name: "Close" });
    if (await close.isVisible().catch(() => false)) await close.click();
    await dialog.waitFor({ state: "detached", timeout: 15_000 });
    await expectSound(page, "/profiles after the setup dialog");
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
  test("the sidebar, body text and primary buttons read at 4.5:1 or more", async ({ page }) => {
    for (const path of ["/dashboard", "/batches", "/templates", "/profiles"]) {
      await page.goto(path);
      await settle(page);
      const low = await page.evaluate(lowContrast);
      expect(low, `${path}: text under 4.5:1 (3:1 for large text)`).toEqual([]);
    }
  });
});

/**
 * Text that reads under WCAG AA on its own background: the sidebar's entries and headings, the
 * body's paragraphs, labels and table text, and every enabled primary button. Colours are the
 * computed ones; a background is found by walking up to the first one painted, translucent
 * washes blended over what is under them. Large text (24 px, or 18.7 px bold) needs 3:1.
 * Runs in the page; returns "ratio  text  (selector)" for each one that fails.
 */
function lowContrast(): string[] {
  type RGBA = [number, number, number, number];
  const parse = (c: string): RGBA => {
    const m = c.match(/rgba?\(([^)]+)\)/);
    if (!m) return [0, 0, 0, 0];
    const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number);
    return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
  };
  const over = (top: RGBA, under: RGBA): RGBA => {
    const a = top[3] + under[3] * (1 - top[3]);
    if (a === 0) return [0, 0, 0, 0];
    const mix = (i: number) => (top[i] * top[3] + under[i] * under[3] * (1 - top[3])) / a;
    return [mix(0), mix(1), mix(2), a];
  };
  const background = (el: Element | null): RGBA => {
    const layers: RGBA[] = [];
    for (let node = el; node; node = node.parentElement) {
      const bg = parse(getComputedStyle(node).backgroundColor);
      if (bg[3] > 0) {
        layers.push(bg);
        if (bg[3] >= 1) break;
      }
    }
    return layers.reduceRight<RGBA>((under, top) => over(top, under), [255, 255, 255, 1]);
  };
  const lum = ([r, g, b]: RGBA) => {
    const f = (v: number) => {
      const c = v / 255;
      return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const ratio = (a: RGBA, b: RGBA) => {
    const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
  };
  const own = (el: Element) => Array.from(el.childNodes).some((n) => n.nodeType === 3 && (n.textContent ?? "").trim().length > 1);
  const hidden = (el: Element) => !!el.closest('[aria-hidden="true"], .sr-only, .animate-pulse, [hidden], svg');
  const targets = [
    ...document.querySelectorAll("aside nav a, aside nav a *, aside nav p"),
    ...document.querySelectorAll("main p, main p *, main label, main label *, main td, main th, main dt, main dd, main li"),
    ...document.querySelectorAll(".btn-primary:not(:disabled), .btn-primary:not(:disabled) *"),
  ];
  const out: string[] = [];
  for (const el of new Set(targets)) {
    if (!own(el) || hidden(el)) continue;
    const style = getComputedStyle(el);
    if (style.visibility === "hidden" || style.display === "none" || Number(style.opacity) === 0) continue;
    const bg = background(el);
    const fg = over(parse(style.color), bg);
    const size = parseFloat(style.fontSize);
    const large = size >= 24 || (size >= 18.66 && Number(style.fontWeight) >= 700);
    const r = ratio(fg, bg);
    if (r < (large ? 3 : 4.5)) {
      out.push(`${r.toFixed(2)}  ${(el.textContent ?? "").trim().slice(0, 40)}  (${el.tagName.toLowerCase()}.${String(el.className).slice(0, 50)})`);
    }
  }
  return out.slice(0, 25);
}
