/**
 * A draft from a profile made by hand, finished on a fake Etsy, in a real browser (375 and 1366 px).
 *
 *   sign in → Profiles → New profile → Start from scratch (the form's options are read from "Etsy" by the real
 *   job) → a design uploaded, the profile chosen for it, a listing written by hand, approved, sent →
 *   the worker makes the draft → the batch page says "drafted" and its counts say so → the request "Etsy" was
 *   sent carries the profile's own values.
 *
 * Its own stack (docker-compose.draft-e2e.yml), started here and stopped afterwards: the production frontend
 * image on localhost:3821, the API and the worker in one container, and "Etsy" answered from memory by the
 * backend tests' own fake (backend/tests/browser_etsy.py; backend/tests/test_browser_etsy.py walks the same
 * path in seconds). Synthetic: one made-up account with two shops; nothing leaves the container and nothing is kept.
 * DRAFT_E2E=off skips it. The screenshot goes to test-results/profiles/ (not committed).
 */
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync } from "node:fs";
import { join, resolve } from "node:path";
import { expect, test, type Page } from "@playwright/test";

const ROOT = [resolve(process.cwd(), ".."), process.cwd()].find((d) => existsSync(join(d, "docker-compose.draft-e2e.yml")))
  ?? resolve(process.cwd(), "..");
const COMPOSE = ["compose", "-p", "listyro-draft-e2e", "-f", join(ROOT, "docker-compose.draft-e2e.yml")];
const APP = "http://localhost:3821";
// The stack's one account (backend/scripts/draft_e2e.py): made up, and gone with the stack.
const EMAIL = "draft-e2e@example.com";
const PASSWORD = "manual-draft-e2e-password";
const SHOTS = "test-results/profiles";

function docker(args: string[], timeout = 600_000): string {
  return execFileSync("docker", args, { cwd: ROOT, encoding: "utf8", timeout, stdio: ["ignore", "pipe", "pipe"] });
}

/** A write as the page itself makes it (the API refuses a write that does not come from the app's own origin). */
async function write(page: Page, method: string, url: string, data: unknown): Promise<{ status: number; body: any }> {
  return page.evaluate(async ([m, u, d]) => {
    const res = await fetch(u as string, { method: m as string, credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify(d) });
    let body: unknown = null;
    try { body = await res.json(); } catch { body = null; }
    return { status: res.status, body };
  }, [method, url, data] as const);
}

async function group(page: Page, name: string): Promise<void> {
  const head = page.getByTestId(`form-group-${name}`).getByRole("button", { name: new RegExp(`^${name}`) }).first();
  if ((await head.getAttribute("aria-expanded")) !== "true") await head.click();
}

test.describe("a draft from a profile made by hand, on a fake Etsy", () => {
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
    // Always from nothing: the account, its profile and its batch are this run's own.
    docker([...COMPOSE, "down", "--volumes"], 120_000);
    docker([...COMPOSE, "up", "-d", "--build", "--wait"], 1_100_000);
  });

  test.afterAll(({}, info) => {
    if (info.project.name === "tablet-768" || process.env.DRAFT_E2E === "off") return;
    docker([...COMPOSE, "down", "--volumes"], 120_000);
  });

  test("from scratch to a finished draft: the row says drafted and Etsy was sent the profile's values", async ({ page }, info) => {
    test.setTimeout(300_000);
    const project = info.project.name;
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(String(e)));

    await page.goto(`${APP}/login`);
    await page.locator("#email").fill(EMAIL);
    await page.locator("#password").fill(PASSWORD);
    await page.locator('button[type="submit"]').click();
    await page.waitForURL(/\/dashboard/, { timeout: 60_000 });

    // --- The profile, typed into the form; what the form offers was read from "Etsy" by the options job -----
    await page.goto(`${APP}/profiles`);
    await page.getByTestId("new-profile-button").click();
    await page.getByTestId("scratch-shop").selectOption({ label: "Pine Ridge Tees" });
    await page.getByTestId("scratch-start").click();
    const form = page.getByTestId("profile-form");
    await expect(form.getByTestId("form-name")).toBeVisible({ timeout: 90_000 });
    await expect(form).toContainText("For Pine Ridge Tees.");
    // Nothing a draft needs is there yet: the button waits, and one line says what for.
    await expect(form.getByTestId("profile-form-save")).toBeDisabled();
    await expect(form.getByTestId("profile-form-missing")).toContainText("Still needed: Profile name, Category, Price");
    await form.getByTestId("form-name").fill("Pine Ridge tees");
    await form.getByTestId("form-category-search").fill("t-shirt");
    await form.getByTestId("form-category-option").filter({ hasText: "Tops & Tees" }).first().click({ timeout: 60_000 });
    await expect(form.getByTestId("form-category-chosen")).toContainText("T-shirts");
    await group(page, "Price & variations");
    await form.getByTestId("form-price").fill("24.5");
    await form.getByTestId("form-price").blur();
    await expect(form.getByTestId("form-price")).toHaveValue("24.50");
    await form.getByTestId("variation-name-0").fill("Size");
    await form.getByTestId("variation-options-0").fill("S, M, L");
    await form.getByTestId("variation-make").click();
    await expect(form.getByTestId("variation-row")).toHaveCount(3);
    await form.getByTestId("variation-price").last().fill("27.25");
    await form.getByTestId("variation-price").last().blur();
    await expect(form.getByTestId("variation-price").first()).toHaveValue("24.50");
    await group(page, "Shipping & policies");
    await form.getByTestId("form-shipping_profile_id").selectOption({ index: 1 });
    await form.getByTestId("form-return_policy_id").selectOption({ index: 1 });
    await expect(form.getByTestId("form-readiness_state_id")).not.toHaveValue("");  // the shop has one: chosen for the seller
    await form.getByTestId("form-partner").selectOption({ index: 1 });
    await group(page, "Details");
    await form.getByTestId("form-materials").fill("Cotton");
    await expect(form.getByTestId("form-attributes")).toBeVisible({ timeout: 30_000 });
    await form.getByLabel("Primary color").selectOption("Black");
    await group(page, "Text");
    await expect(form.getByTestId("profile-form-missing")).toContainText("Still needed: Description.");
    await form.getByTestId("form-description").fill("A soft cotton tee, printed to order.\n\nCare: wash cold, hang dry.");
    await form.getByTestId("form-tags").fill("soft tee, gift for her");
    await expect(form.getByTestId("profile-form-missing")).toHaveCount(0);
    await form.getByTestId("profile-form-save").click();
    await expect(form).toBeHidden({ timeout: 30_000 });
    const profile = ((await (await page.request.get(`${APP}/api/profiles`)).json()) as { id: string; name: string; manual: boolean; connection_id: string }[])
      .find((p) => p.name === "Pine Ridge tees")!;
    expect(profile?.manual).toBe(true);
    const shop = profile.connection_id;
    const values = (await (await page.request.get(`${APP}/api/profiles/${profile.id}/values`)).json()).values;

    // --- One design, the profile chosen for it, a listing written by hand, approved, sent ------------------------
    const created = await write(page, "POST", "/api/batches", { connection_id: shop });
    expect(created.status, JSON.stringify(created.body)).toBe(201);
    const batch = created.body.id as string;
    const uploaded = await page.evaluate(async (id) => {
      const canvas = document.createElement("canvas");
      canvas.width = 900; canvas.height = 900;
      const ctx = canvas.getContext("2d")!;
      ctx.fillStyle = "#f4f1ea"; ctx.fillRect(0, 0, 900, 900);
      ctx.fillStyle = "#14162b"; ctx.font = "700 96px sans-serif"; ctx.fillText("Pine ridge", 180, 470);
      const blob: Blob = await new Promise((done) => canvas.toBlob((b) => done(b!), "image/png"));
      const body = new FormData();
      body.append("file", blob, "PRF100.png");
      body.append("grouping", "one");
      return (await fetch(`/api/batches/${id}/assets`, { method: "POST", credentials: "include", body })).status;
    }, batch);
    expect(uploaded).toBe(201);
    expect((await write(page, "POST", `/api/batches/${batch}/finalize`, {})).status).toBe(200);
    await expect.poll(async () => {
      const detail = await (await page.request.get(`${APP}/api/batches/${batch}`)).json();
      const assets = (detail.assets ?? []) as { status: string }[];
      return assets.length > 0 && assets.every((a) => a.status === "processed");
    }, { timeout: 90_000, intervals: [1000] }).toBe(true);
    const assigned = await write(page, "PUT", `/api/batches/${batch}/groups`, { profile_id: profile.id });
    expect(assigned.status, JSON.stringify(assigned.body)).toBe(200);
    expect((await write(page, "POST", `/api/batches/${batch}/manual`, {})).status).toBe(200);
    const listing = ((await (await page.request.get(`${APP}/api/batches/${batch}/content`)).json()) as { id: string; tags: string[] }[])[0];
    expect(listing.tags).toEqual(["soft tee", "gift for her"]);  // the profile's default tags start it
    const title = "Pine Ridge Camping Shirt for Hikers";
    expect((await write(page, "PATCH", `/api/content/${listing.id}`, { title })).status).toBe(200);
    expect((await write(page, "POST", `/api/content/${listing.id}/approve`, { approved: true, connection_id: null })).status).toBe(200);
    const sent = await write(page, "POST", `/api/batches/${batch}/send`, { when: "drafts" });
    expect(sent.status, JSON.stringify(sent.body)).toBe(200);
    expect(sent.body.jobs.length, JSON.stringify(sent.body)).toBe(1);

    // --- The worker makes the draft on the fake Etsy; the batch page says so -----------------------------------------
    await expect.poll(async () => ((await (await page.request.get(`${APP}/api/__etsy/sent`)).json()).sent as unknown[]).length,
      { timeout: 120_000, intervals: [1000] }).toBe(1);
    await page.goto(`${APP}/batches/${batch}?shop=${shop}`);
    const workspace = page.getByTestId("shop-workspace");
    await expect(workspace).toBeVisible({ timeout: 60_000 });
    await expect.poll(async () => {
      await page.reload();
      return (await workspace.getByTestId("shop-bar-counts").textContent({ timeout: 30_000 })) ?? "";
    }, { timeout: 120_000, intervals: [2000] }).toMatch(/1 drafted|drafted 1/);
    const row = workspace.getByTestId("workspace-row").first();
    await expect(row).toContainText("drafted");
    await expect(row.getByTestId("workspace-row-locked")).toContainText("View on Etsy");
    await expect(workspace.getByTestId("shop-bar-failed")).toHaveCount(0);
    await expect(workspace.getByTestId("workspace-row-failed")).toHaveCount(0);

    // --- What "Etsy" was sent is the profile's own values ------------------------------------------------------------------
    const record = ((await (await page.request.get(`${APP}/api/__etsy/sent`)).json()).sent as { shop_id: number; form: Record<string, string> }[])[0];
    const sentForm = record.form;
    expect(record.shop_id).toBe(900);
    expect(sentForm.title).toBe(title);
    expect(sentForm.taxonomy_id).toBe(String(values.taxonomy_id));
    expect([sentForm.who_made, sentForm.when_made]).toEqual([values.who_made, values.when_made]);
    expect(Number(sentForm.price)).toBe(24.5);
    expect(sentForm.quantity).toBe("999");
    expect(sentForm.shipping_profile_id).toBe(String(values.shipping_profile_id));
    expect(sentForm.return_policy_id).toBe(String(values.return_policy_id));
    expect(sentForm.readiness_state_id).toBe(String(values.readiness_state_id));
    expect(sentForm.production_partner_ids).toBe(String(values.production_partner_ids[0]));
    expect(sentForm.materials).toBe("Cotton");
    expect(sentForm.tags.split(",")).toEqual(["soft tee", "gift for her"]);
    expect(sentForm.description).toContain("A soft cotton tee, printed to order.");

    mkdirSync(SHOTS, { recursive: true });
    await row.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${SHOTS}/${project}-6-draft-made-on-fake-etsy.png`, fullPage: false });
    expect(errors, "no error on any page of the flow").toEqual([]);
  });
});
