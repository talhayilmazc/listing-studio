/**
 * Mockup templates: a photo of one product is one print area, the seller has the final say, and an upload that
 * fails says so and is sent again by itself (18 November 2026).
 *
 * The customer's report: a photo with ONE shirt was given SEVERAL print areas; and of five photos chosen, the
 * fifth never arrived and its row sat at "100%" with no word.
 *
 *   1. An upload that does not arrive shows "Couldn't upload <file>. Try again"; Try again sends that file only,
 *      the row says "Processing…" (never 100%) while the server works, and the photo of one shirt, analysed by
 *      the worker, opens with ONE print area.
 *   2. A photo of two shirts is found as two; "This photo has one product" makes it one, in one click; the area
 *      moved by hand and confirmed is still exactly there after the photo's layout is asked for again.
 *
 * Real clicks and keys only. Runs with the layout suite (LAYOUT_EMAIL / LAYOUT_PASSWORD: the demo admin from
 * `seed-demo --admin`) at 375 and 1366 px, against a running worker (it analyses the photos; no Etsy call, no AI
 * call). The photos are drawn here: no real garment photo, no seller's file.
 */
import { expect, test, type Page } from "@playwright/test";
import sharp from "sharp";
import { signIn } from "./session";

const EMAIL = process.env.LAYOUT_EMAIL;
const PASSWORD = process.env.LAYOUT_PASSWORD;

/** A flat-lay tee (the outline the backend's own test photos use) inside a box. */
function tee(x: number, y: number, w: number, h: number, colour: string): string {
  const points = [[0.36, 0.10], [0.43, 0.13], [0.50, 0.15], [0.57, 0.13], [0.64, 0.10], [0.86, 0.20], [0.80, 0.36], [0.71, 0.32],
    [0.71, 0.92], [0.29, 0.92], [0.29, 0.32], [0.20, 0.36], [0.14, 0.20]];
  return `<polygon points="${points.map(([px, py]) => `${x + px * w},${y + py * h}`).join(" ")}" fill="${colour}"/>`;
}

async function png(name: string, width: number, height: number, background: string, shapes: string) {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"><rect width="100%" height="100%" fill="${background}"/>${shapes}</svg>`;
  return { name, mimeType: "image/png", buffer: await sharp(Buffer.from(svg)).png().toBuffer() };
}

/** The same one shirt as a photo from a camera: 2400 x 2800 with sensor grain, so the PNG is over 8 MB and goes up in pieces. */
async function oneShirt() {
  const width = 2400, height = 2800;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"><rect width="100%" height="100%" fill="#c8cbcf"/>${tee(0, 0, width, height, "#d63c3c")}</svg>`;
  const raw = await sharp(Buffer.from(svg)).removeAlpha().raw().toBuffer();
  let seed = 20261118;
  for (let i = 0; i < raw.length; i += 1) {  // fixed-seed grain of a few levels: nothing a camera would not add
    seed = (seed * 1664525 + 1013904223) >>> 0;
    raw[i] = Math.max(0, Math.min(255, raw[i] + ((seed >>> 24) % 13) - 6));
  }
  const buffer = await sharp(raw, { raw: { width, height, channels: 3 } }).png({ compressionLevel: 1 }).toBuffer();
  return { name: "one-shirt.png", mimeType: "image/png", buffer };
}
const twoShirts = () => png("two-shirts.png", 1400, 760, "#e8e9ec", tee(40, 10, 640, 740, "#c83c3c") + tee(740, 10, 640, 740, "#325ac8"));

type Photo = { id: string; status: string; layout: string; layout_source: string; confirmed: boolean; template_id: string;
  areas: { print_area: number[][]; source: string }[] };

const photoOf = async (page: Page, id: string): Promise<Photo> => (await page.request.get(`/api/mockup-photos/${id}`)).json();

/** Choose a file for a new template of this name, confirm the rights, and press Add. */
async function add(page: Page, file: Awaited<ReturnType<typeof png>>, name: string): Promise<void> {
  await page.goto("/mockups/templates");
  await page.getByTestId("template-file-files").setInputFiles([file]);
  await page.getByTestId("template-name").fill(name);
  await page.getByTestId("template-rights").check();
  await page.getByTestId("template-add").click();
}

const isPhotoPost = (url: string, method: string) => method === "POST" && /\/api\/mockup-templates\/[^/]+\/photos$/.test(new URL(url).pathname);

test.describe("mockup templates: one product, one area; a failed upload says so", () => {
  test.skip(!EMAIL || !PASSWORD, "set LAYOUT_EMAIL and LAYOUT_PASSWORD (the demo admin from `seed-demo --admin`) to run these");
  const made: string[] = [];

  test.beforeEach(async ({ page }, info) => {
    test.skip(info.project.name === "tablet-768", "a desktop and a phone");
    test.setTimeout(300_000);
    await signIn(page, EMAIL!, PASSWORD!);
  });
  test.afterEach(async ({ page }) => {
    for (const template of made.splice(0)) {
      await page.evaluate((id) => fetch(`/api/mockup-templates/${id}`, { method: "DELETE", credentials: "include" }), template);
    }
  });

  test("a failed upload shows Try again, Try again succeeds, and a single-product photo gets one area", async ({ page }) => {
    const file = await oneShirt();
    expect(file.buffer.length, "a photo over 8 MB: it goes up in pieces").toBeGreaterThan(8 * 1024 * 1024);
    // Every piece arrives; the request that makes them a photo is lost once (as the fifth photo was on
    // production), and answered after a moment the second time.
    const pieces: string[] = [];
    let lost = 0;
    page.on("request", (r) => {
      if (r.method() === "PUT" && /\/api\/mockup-templates\/[^/]+\/photos$/.test(new URL(r.url()).pathname)) pieces.push(new URL(r.url()).searchParams.get("offset")!);
    });
    await page.route("**/api/mockup-templates/*/photos/assembled", async (route) => {
      if (lost === 0) {
        lost += 1;
        return route.abort("connectionreset");
      }
      await new Promise((r) => setTimeout(r, 3000));
      return route.continue();
    });
    await add(page, file, `One shirt ${Date.now()}`);
    const again = page.getByTestId("template-row-again");
    const state = page.getByTestId("template-row-state");
    await expect(again).toHaveText("Couldn't upload one-shirt.png. Try again", { timeout: 120_000 });
    await expect(state).toHaveText("Not added");
    expect(pieces.length, "sent in pieces of 8 MB").toBeGreaterThanOrEqual(2);
    expect(pieces[0]).toBe("0");

    const arrived = page.waitForResponse((r) => r.url().includes("/photos/assembled") && r.status() === 201, { timeout: 120_000 });
    await again.click();  // this file only
    // The bytes are with the server: the row says so, and never sits at "100%".
    await expect(state).toHaveText("Processing…", { timeout: 60_000 });
    const photo = (await (await arrived).json()) as Photo;
    made.push(photo.template_id);
    expect(lost).toBe(1);

    // The worker finds the one shirt: one print area, offered in the editor for the seller's check.
    await expect.poll(async () => (await photoOf(page, photo.id)).status, { timeout: 240_000, intervals: [2000] }).toBe("ready");
    const ready = await photoOf(page, photo.id);
    expect(ready.areas.length, "one product, one area").toBe(1);
    expect(ready.layout).toBe("single");
    const editor = page.getByTestId("area-editor");
    await expect(editor).toBeVisible({ timeout: 60_000 });
    await expect(editor.getByTestId("editor-area")).toHaveCount(1);
    await expect(editor.getByTestId("editor-one-product")).toHaveCount(0);  // nothing to merge: not offered
    await editor.getByTestId("editor-confirm").click();
    await expect.poll(async () => (await photoOf(page, photo.id)).confirmed, { timeout: 30_000 }).toBe(true);
  });

  test("This photo has one product makes one area in one click, and a hand-set area survives a later analysis", async ({ page }) => {
    const arrived = page.waitForResponse((r) => isPhotoPost(r.url(), r.request().method()) && r.status() === 201, { timeout: 60_000 });
    await add(page, await twoShirts(), `Two shirts ${Date.now()}`);
    const photo = (await (await arrived).json()) as Photo;
    made.push(photo.template_id);
    await expect.poll(async () => (await photoOf(page, photo.id)).status, { timeout: 240_000, intervals: [2000] }).toBe("ready");
    const found = await photoOf(page, photo.id);
    expect(found.areas.length, "two shirts are found as two").toBe(2);

    const editor = page.getByTestId("area-editor");
    await expect(editor).toBeVisible({ timeout: 60_000 });
    await expect(editor.getByTestId("editor-area")).toHaveCount(2);
    // One click: the seller's word.
    await editor.getByTestId("editor-one-product").click();
    await expect(editor.getByTestId("editor-area")).toHaveCount(1);
    await expect(editor.getByTestId("editor-layout")).toHaveValue("single");
    const one = await photoOf(page, photo.id);
    expect([one.areas.length, one.layout, one.layout_source, one.areas[0].source]).toEqual([1, "single", "seller", "seller"]);

    // Moved by hand (the keyboard, as a seller may), then "Looks right".
    // (A click on the editor's own title puts the keyboard in the editor; on a phone the area's 44 px handles
    // cover the small area itself, and a seller drags those.)
    await editor.locator("header").click({ position: { x: 12, y: 12 } });
    for (let i = 0; i < 4; i += 1) await page.keyboard.press("Shift+ArrowRight");
    await editor.getByTestId("editor-confirm").click();
    await expect.poll(async () => (await photoOf(page, photo.id)).confirmed, { timeout: 30_000 }).toBe(true);
    const mine = await photoOf(page, photo.id);
    expect(mine.areas.length).toBe(1);
    expect(mine.areas[0].print_area[0][0], "the area moved to the right").toBeGreaterThan(one.areas[0].print_area[0][0]);

    // The photo's layout is asked for again (its suggestions are chosen again from the analysis): the seller's
    // area is exactly where it was put, and still the only one.
    if (await editor.isVisible()) await editor.getByTestId("editor-close").click();
    const layout = page.getByTestId("template-photo-layout").first();
    if (!(await layout.isVisible())) await page.getByTestId("template-toggle").first().click();
    await expect(layout).toBeVisible({ timeout: 30_000 });
    for (const value of ["several", "single"]) {
      const saved = page.waitForResponse((r) => r.url().includes(`/api/mockup-photos/${photo.id}`) && r.request().method() === "PATCH");
      await layout.selectOption(value);
      expect((await saved).status()).toBe(200);
    }
    const after = await photoOf(page, photo.id);
    expect(after.areas.map((a) => a.print_area)).toEqual(mine.areas.map((a) => a.print_area));
    expect(after.areas[0].source).toBe("seller");
  });
});
