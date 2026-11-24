/**
 * A listing's video (feature/listing-video), in a real browser at 375 and 1366 px, on a fake Etsy.
 *
 *   a listing on the review page → its "Video" slot → a 22 second video is refused in plain words → an 8 second
 *   one goes up, is read by the worker and shows its poster frame and "0:08" → the draft is sent → the fake Etsy
 *   was sent the video, and the batch row says "Video attached to the draft" → a second batch with no video:
 *   nothing about it changes and Etsy is sent no video.
 *
 * Its stack is the draft test's (docker-compose.draft-e2e.yml; frontend/e2e/manualDraft.spec.ts): the production
 * frontend image, the API and the worker in one container, "Etsy" answered from memory by the backend tests' own
 * fake, one made-up account. The two videos are our own test pattern (e2e/fixtures/; made by ffmpeg's "testsrc",
 * nobody's footage). DRAFT_E2E=off skips it. Screenshots go to test-results/video/ (not committed).
 */
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync } from "node:fs";
import { join, resolve } from "node:path";
import { expect, test, type Page } from "@playwright/test";

const ROOT = [resolve(process.cwd(), ".."), process.cwd()].find((d) => existsSync(join(d, "docker-compose.draft-e2e.yml")))
  ?? resolve(process.cwd(), "..");
const COMPOSE = ["compose", "-p", "listyro-draft-e2e", "-f", join(ROOT, "docker-compose.draft-e2e.yml")];
const APP = "http://localhost:3821";
const EMAIL = "draft-e2e@example.com";
const PASSWORD = "manual-draft-e2e-password";
const SHOTS = "test-results/video";
const FIXTURES = join(ROOT, "frontend", "e2e", "fixtures");
const GOOD = join(FIXTURES, "listing-video-8s.mp4");
const LONG = join(FIXTURES, "listing-video-22s.mp4");
const SMALL = join(FIXTURES, "listing-video-small.mp4");  // 320 x 568

function docker(args: string[], timeout = 600_000): string {
  return execFileSync("docker", args, { cwd: ROOT, encoding: "utf8", timeout, stdio: ["ignore", "pipe", "pipe"] });
}

async function write(page: Page, method: string, url: string, data: unknown): Promise<{ status: number; body: any }> {
  return page.evaluate(async ([m, u, d]) => {
    const res = await fetch(u as string, { method: m as string, credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify(d) });
    let body: unknown = null;
    try { body = await res.json(); } catch { body = null; }
    return { status: res.status, body };
  }, [method, url, data] as const);
}

const read = async (page: Page, url: string): Promise<any> => (await page.request.get(`${APP}${url}`)).json();

async function shot(page: Page, name: string, project: string): Promise<void> {
  mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: `${SHOTS}/${project}-${name}.png`, fullPage: false });
}

/** A profile made by hand for the shop, from what the form's options job read from the fake Etsy. */
async function profile(page: Page, shop: string): Promise<string> {
  await expect.poll(async () => (await read(page, `/api/profile-form/options?shop=${shop}`)).reading, { timeout: 90_000, intervals: [1500] }).toBe(false);
  await expect.poll(async () => (await read(page, `/api/profile-form/category?shop=${shop}&taxonomy_id=9004`)).reading, { timeout: 60_000, intervals: [1500] }).toBe(false);
  const options = await read(page, `/api/profile-form/options?shop=${shop}`);
  const made = await write(page, "POST", "/api/profile-form", {
    connection_id: shop, name: `Video tees ${Date.now().toString().slice(-5)}`, listing_style: "search", title_prefix: "", default_tags: ["soft tee"], personalization: null,
    values: {
      taxonomy_id: 9004, who_made: "i_did", when_made: "made_to_order", is_supply: false, price: 24.5, quantity: 999, variations: null,
      shipping_profile_id: options.shipping[0].id, return_policy_id: options.returns[0].id, readiness_state_id: options.readiness[0].id,
      production_partner_ids: [], materials: [], attributes: {}, description: "A soft cotton tee, printed to order.",
    },
  });
  expect(made.status, JSON.stringify(made.body)).toBe(201);
  return made.body.id as string;
}

/** One design, written by hand with the profile; returns the batch and its listing. */
async function listing(page: Page, shop: string, profileId: string, name: string): Promise<{ batch: string; content: string }> {
  const created = await write(page, "POST", "/api/batches", { connection_id: shop });
  expect(created.status, JSON.stringify(created.body)).toBe(201);
  const batch = created.body.id as string;
  const uploaded = await page.evaluate(async ([id, file]) => {
    const canvas = document.createElement("canvas");
    canvas.width = 900; canvas.height = 900;
    const ctx = canvas.getContext("2d")!;
    ctx.fillStyle = "#f4f1ea"; ctx.fillRect(0, 0, 900, 900);
    ctx.fillStyle = "#14162b"; ctx.font = "700 96px sans-serif"; ctx.fillText("Pine ridge", 180, 470);
    const blob: Blob = await new Promise((done) => canvas.toBlob((b) => done(b!), "image/png"));
    const body = new FormData();
    body.append("file", blob, file);
    body.append("grouping", "one");
    return (await fetch(`/api/batches/${id}/assets`, { method: "POST", credentials: "include", body })).status;
  }, [batch, name] as const);
  expect(uploaded).toBe(201);
  expect((await write(page, "POST", `/api/batches/${batch}/finalize`, {})).status).toBe(200);
  await expect.poll(async () => {
    const assets = ((await read(page, `/api/batches/${batch}`)).assets ?? []) as { status: string }[];
    return assets.length > 0 && assets.every((a) => a.status === "processed");
  }, { timeout: 90_000, intervals: [1000] }).toBe(true);
  expect((await write(page, "PUT", `/api/batches/${batch}/groups`, { profile_id: profileId })).status).toBe(200);
  expect((await write(page, "POST", `/api/batches/${batch}/manual`, {})).status).toBe(200);
  const content = ((await read(page, `/api/batches/${batch}/content`)) as { id: string }[])[0].id;
  expect((await write(page, "PATCH", `/api/content/${content}`, { title: "Pine Ridge Camping Shirt for Hikers" })).status).toBe(200);
  expect((await write(page, "POST", `/api/content/${content}/approve`, { approved: true, connection_id: null })).status).toBe(200);
  return { batch, content };
}

test.describe("a listing's video, on a fake Etsy", () => {
  test.beforeEach(({}, info) => {
    test.skip(info.project.name === "tablet-768", "a desktop and a phone");
    test.skip(process.env.DRAFT_E2E === "off", "DRAFT_E2E=off: the stack was not started");
  });

  test.beforeAll(({}, info) => {
    if (info.project.name === "tablet-768" || process.env.DRAFT_E2E === "off") return;
    test.setTimeout(1_200_000);
    try {
      docker(["image", "inspect", "listyro/frontend:latest"], 30_000);
    } catch {
      throw new Error("This test needs the production frontend image: docker compose -f docker-compose.prod.yml build frontend");
    }
    docker([...COMPOSE, "down", "--volumes"], 120_000);
    docker([...COMPOSE, "up", "-d", "--build", "--wait"], 1_100_000);
  });

  test.afterAll(({}, info) => {
    if (info.project.name === "tablet-768" || process.env.DRAFT_E2E === "off") return;
    docker([...COMPOSE, "down", "--volumes"], 120_000);
  });

  test("add a video on the review card, a wrong length is said, the draft gets the video; a batch without one is as it was", async ({ page }, info) => {
    test.setTimeout(420_000);
    const project = info.project.name;
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(String(e)));

    await page.goto(`${APP}/login`);
    await page.locator("#email").fill(EMAIL);
    await page.locator("#password").fill(PASSWORD);
    await page.locator('button[type="submit"]').click();
    await page.waitForURL(/\/dashboard/, { timeout: 60_000 });
    const shops = (await read(page, "/api/shops")).shops as { id: string; name: string }[];
    const shop = shops.find((s) => s.name === "Pine Ridge Tees")!.id;
    const profileId = await profile(page, shop);
    const withVideo = await listing(page, shop, profileId, "PRV100.png");
    const without = await listing(page, shop, profileId, "PRV200.png");

    // --- The review card: one quiet line, then a video Etsy would not take, then one it takes ------------------------
    await page.goto(`${APP}/batches/${withVideo.batch}/review`);
    const slot = page.getByTestId("video-slot").first();
    await expect(slot).toBeVisible({ timeout: 60_000 });
    await expect(slot).toHaveAttribute("data-video", "none");
    await expect(slot.getByTestId("video-add")).toHaveText("Add video");
    await slot.getByTestId("video-input").setInputFiles(LONG);
    await expect(slot.getByTestId("video-error")).toContainText("This video is 22 s; Etsy takes 3–15 s.", { timeout: 90_000 });
    await slot.scrollIntoViewIfNeeded();
    await shot(page, "2-wrong-length", project);
    await expect(slot.getByTestId("video-poster")).toHaveCount(0);  // nothing was added
    await expect(slot.getByTestId("video-silent")).toHaveCount(0);
    // A frame Etsy would not take: said in the same plain way.
    await slot.getByTestId("video-input").setInputFiles(SMALL);
    await expect(slot.getByTestId("video-error")).toContainText("This video is 320 px; Etsy needs at least 500 px.", { timeout: 90_000 });
    await shot(page, "2b-too-small", project);
    await expect(slot.getByTestId("video-poster")).toHaveCount(0);

    await slot.getByTestId("video-input").setInputFiles(GOOD);
    await expect(slot.locator('img[data-testid="video-poster"]')).toBeVisible({ timeout: 120_000 });  // the worker's poster frame
    await expect(slot.getByTestId("video-duration")).toContainText("0:08");
    await expect(slot.getByTestId("video-duration")).toContainText("listing-video-8s.mp4");
    await expect(slot).toHaveAttribute("data-video", "ready");
    expect(await slot.locator('img[data-testid="video-poster"]').evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true);
    await expect(slot.getByTestId("video-add")).toHaveText("Replace");
    await expect(slot.getByTestId("video-silent")).toHaveText("Etsy shows listing videos without sound.");
    await expect(slot.getByTestId("video-remove")).toBeVisible();
    // Nothing is wider than the screen with the slot there.
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    await slot.scrollIntoViewIfNeeded();
    await shot(page, "1-review-card-video-slot", project);
    expect(((await read(page, "/api/__etsy/videos")).videos as unknown[]).length).toBe(0);  // nothing goes to Etsy before the draft

    // --- The draft: made with its images, then the video -----------------------------------------------------------------
    const sent = await write(page, "POST", `/api/batches/${withVideo.batch}/send`, { when: "drafts" });
    expect(sent.status, JSON.stringify(sent.body)).toBe(200);
    await expect.poll(async () => ((await read(page, "/api/__etsy/videos")).videos as unknown[]).length, { timeout: 120_000, intervals: [1000] }).toBe(1);
    const drafts = (await read(page, "/api/__etsy/sent")).sent as { listing_id: number }[];
    const video = ((await read(page, "/api/__etsy/videos")).videos as { listing_id: number; name: string; bytes: number; content_type: string }[])[0];
    expect(video.listing_id).toBe(drafts[0].listing_id);
    expect(video.name).toBe("listing-video-8s.mp4");
    expect(video.content_type).toBe("multipart/form-data");
    expect(video.bytes).toBeGreaterThan(10_000);  // the file itself went up

    await page.goto(`${APP}/batches/${withVideo.batch}`);
    const row = page.getByTestId("video-slot").first();
    await expect(row.getByTestId("video-draft")).toHaveAttribute("data-state", "attached", { timeout: 60_000 });
    await expect(row.getByTestId("video-draft")).toContainText("Video attached to the draft");
    await expect(row.locator('img[data-testid="video-poster"]')).toBeVisible();
    await row.scrollIntoViewIfNeeded();
    await shot(page, "3-batch-row-draft-with-video", project);
    await page.goto(`${APP}/batches/${withVideo.batch}/review`);
    await expect(page.getByTestId("video-slot").first().getByTestId("video-draft")).toContainText("Video attached to the draft", { timeout: 60_000 });
    await page.getByTestId("video-slot").first().scrollIntoViewIfNeeded();
    await shot(page, "4-review-card-draft-with-video", project);

    // --- A batch with no video: one quiet line, and nothing else about it changes -----------------------------------------
    await page.goto(`${APP}/batches/${without.batch}/review`);
    const plain = page.getByTestId("video-slot").first();
    await expect(plain).toHaveAttribute("data-video", "none", { timeout: 60_000 });
    await expect(plain.getByTestId("video-poster")).toHaveCount(0);
    await expect(plain.getByTestId("video-draft")).toHaveCount(0);
    expect((await read(page, `/api/batches/${without.batch}/videos`)).videos).toEqual([]);
    const plainSent = await write(page, "POST", `/api/batches/${without.batch}/send`, { when: "drafts" });
    expect(plainSent.status, JSON.stringify(plainSent.body)).toBe(200);
    await expect.poll(async () => ((await read(page, "/api/__etsy/sent")).sent as unknown[]).length, { timeout: 120_000, intervals: [1000] }).toBe(2);
    await page.goto(`${APP}/batches/${without.batch}`);
    await expect(page.getByTestId("video-slot").first()).toHaveAttribute("data-video", "none", { timeout: 60_000 });
    await expect(page.getByText("Drafted").first()).toBeVisible({ timeout: 60_000 });
    await expect(page.getByTestId("video-draft")).toHaveCount(0);
    expect(((await read(page, "/api/__etsy/videos")).videos as unknown[]).length).toBe(1);  // still only the first listing's
    await shot(page, "5-batch-without-video", project);

    expect(errors, "no error on any page of the flow").toEqual([]);
  });
});
