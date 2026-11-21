/**
 * Profiles made and edited by hand (feature/manual-profiles), in a real browser at 375 and 1366 px.
 *
 *   Profiles from the sidebar → a profile from scratch → a copy to a shop that has no production partner →
 *   a variation price edited → a value of a listing-following profile edited ("your value") → the new profile
 *   used on a batch and its draft sent.
 *
 * Real clicks and typing. Runs with the layout suite (LAYOUT_EMAIL / LAYOUT_PASSWORD: the demo admin from
 * `seed-demo --admin`, whose shops carry sample form options: backend/app/ops/profile_form_demo.py). A
 * development stack never calls Etsy (ETSY_CALLS_BLOCKED), so the last step ends where the draft job is queued
 * and the batch page says what became of it; the draft itself, against a fake Etsy and Etsy's own request
 * schema, is backend/tests/test_manual_profiles.py. Screenshots go to test-results/profiles/ (not committed).
 */
import { mkdirSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";
import { signIn } from "./session";

const EMAIL = process.env.LAYOUT_EMAIL;
const PASSWORD = process.env.LAYOUT_PASSWORD;
const SHOTS = "test-results/profiles";

type Profile = { id: string; name: string; manual?: boolean; connection_id: string; own_fields?: string[]; confirmed: boolean; is_fresh: boolean };

async function shot(page: Page, name: string, project: string): Promise<void> {
  mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: `${SHOTS}/${project}-${name}.png`, fullPage: false });
}

/** Open a group of the form (a closed group keeps what was typed). */
async function group(page: Page, name: string): Promise<void> {
  const section = page.getByTestId(`form-group-${name}`);
  const head = section.getByRole("button", { name: new RegExp(`^${name.replace("&", "&")}`) }).first();
  if ((await head.getAttribute("aria-expanded")) !== "true") await head.click();
}

const profiles = async (page: Page): Promise<Profile[]> => (await page.request.get("/api/profiles")).json();

/** A write as the page itself makes it (the API refuses a write that does not come from the app's own origin). */
async function write(page: Page, method: string, url: string, data: unknown): Promise<{ status: number; body: any }> {
  return page.evaluate(async ([m, u, d]) => {
    const res = await fetch(u as string, { method: m as string, credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify(d) });
    let body: unknown = null;
    try { body = await res.json(); } catch { body = null; }
    return { status: res.status, body };
  }, [method, url, data] as const);
}

test.describe("profiles made and edited by hand", () => {
  test.skip(!EMAIL || !PASSWORD, "set LAYOUT_EMAIL and LAYOUT_PASSWORD (the demo admin from `seed-demo --admin`) to run these");
  const made: string[] = [];
  const batches: string[] = [];

  test.beforeEach(async ({ page }, info) => {
    test.skip(info.project.name === "tablet-768", "a desktop and a phone");
    test.setTimeout(240_000);
    await signIn(page, EMAIL!, PASSWORD!);
  });
  test.afterEach(async ({ page }) => {
    for (const id of batches.splice(0)) {
      await page.evaluate((bid) => fetch(`/api/batches/${bid}`, { method: "DELETE", credentials: "include" }), id);
    }
    for (const id of made.splice(0)) {
      await page.evaluate((pid) => fetch(`/api/profiles/${pid}`, { method: "DELETE", credentials: "include" }), id);
    }
  });

  test("from the sidebar: from scratch, a copy to an empty shop, an edited price, your value, and a draft sent", async ({ page }, info) => {
    const project = info.project.name;
    const stamp = Date.now().toString().slice(-6);
    const phone = project === "phone-375";

    // The dev server's own corner indicator sits over the foot of a full-screen form on a phone (it is not in
    // the production image): it takes no clicks here. A real error on the page still fails the test.
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    await page.addInitScript(() => {
      const style = document.createElement("style");
      style.textContent = "nextjs-portal { pointer-events: none !important; }";
      document.addEventListener("DOMContentLoaded", () => document.head.appendChild(style));
    });

    // 1. Profiles is in the menu, directly under Shops, and is the active entry on its page.
    await page.goto("/dashboard");
    if (phone) await page.getByRole("button", { name: "Open navigation" }).click();
    const links = page.getByTestId("rail-link");
    await expect(links.filter({ hasText: "Profiles" }).first()).toBeVisible({ timeout: 60_000 });
    const labels = (await links.allTextContents()).map((t) => t.replace(/\d+/g, "").trim()).filter(Boolean);
    expect(labels.indexOf("Profiles"), labels.join(" | ")).toBe(labels.indexOf("Shops") + 1);
    await shot(page, "1-sidebar", project);
    await links.filter({ hasText: "Profiles" }).first().click();
    await page.waitForURL("**/profiles");
    if (phone) await page.getByRole("button", { name: "Open navigation" }).click();
    await expect(page.getByTestId("rail-link").filter({ hasText: "Profiles" }).first()).toHaveAttribute("aria-current", "page");
    await expect(page.getByTestId("rail-link").filter({ hasText: "Settings" }).first()).not.toHaveAttribute("aria-current", "page");
    if (phone) await page.getByRole("button", { name: "Close navigation" }).click();
    await expect(page.locator("body")).toHaveJSProperty("scrollWidth", await page.evaluate(() => document.body.clientWidth));

    // 2. A profile from scratch, for the first shop.
    const before = await profiles(page);
    await page.getByTestId("new-profile-button").click();
    await expect(page.getByTestId("new-profile-choices")).toBeVisible();
    await page.getByTestId("scratch-start").click();
    const form = page.getByTestId("profile-form");
    await expect(form.getByTestId("form-name")).toBeVisible({ timeout: 60_000 });
    const scratchName = `Hand tees ${stamp}`;
    await form.getByTestId("form-name").fill(scratchName);
    await form.getByTestId("form-category-search").fill("t-shirt");
    await form.getByTestId("form-category-option").first().click();
    await expect(form.getByTestId("form-category-chosen")).toContainText("T-shirts");
    await group(page, "Price & variations");
    await form.getByTestId("form-price").fill("24.50");
    await form.getByTestId("variation-name-0").fill("Size");
    await form.getByTestId("variation-options-0").fill("S, M, L");
    await form.getByTestId("variation-make").click();
    await expect(form.getByTestId("variation-row")).toHaveCount(3);
    await shot(page, "2-scratch-form", project);
    // Saved before its shipping is chosen: refused in plain words, under the field, and nothing is made.
    await form.getByTestId("profile-form-save").click();
    await expect(form.getByTestId("profile-form-message")).toContainText("Not saved");
    await group(page, "Shipping & policies");
    await form.getByTestId("form-shipping_profile_id").selectOption({ index: 1 });
    await form.getByTestId("form-return_policy_id").selectOption({ index: 1 });
    await expect(form.getByTestId("form-readiness_state_id")).not.toHaveValue("");  // the shop has one: chosen for the seller
    await form.getByTestId("form-partner").selectOption({ index: 1 });
    await group(page, "Text");
    await form.getByTestId("form-description").fill("A soft tee, printed to order.\n\nCare: wash cold, hang dry.");
    await form.getByTestId("form-tags").fill("soft tee, gift for her");
    await form.getByTestId("profile-form-save").click();
    await expect(form).toBeHidden({ timeout: 30_000 });
    const scratch = (await profiles(page)).find((p) => p.name === scratchName)!;
    expect(scratch, "the profile was made").toBeTruthy();
    made.push(scratch.id);
    expect([scratch.manual, scratch.confirmed, scratch.is_fresh]).toEqual([true, true, true]);
    const card = page.locator(`#profile-${scratch.id}`);
    await expect(card).toContainText("made by hand");
    await expect(card).toContainText("never needs a refresh");
    await expect(card.getByRole("button", { name: /Refresh/ })).toHaveCount(0);

    // 3. A copy of it for the account's new, empty shop (the last one): no production partner there.
    const shops = (await (await page.request.get("/api/shops")).json()).shops as { id: string; name: string }[];
    let empty = shops[shops.length - 1];
    for (const s of shops) {
      const offered = await (await page.request.get(`/api/profile-form/options?shop=${s.id}`)).json();
      if (!offered.reading && offered.partners.length === 0) empty = s;
    }
    expect(empty.id).not.toBe(scratch.connection_id);
    await page.getByTestId("new-profile-button").click();
    await page.getByTestId("copy-source").selectOption(scratch.id);
    await page.getByTestId("copy-target").selectOption(empty.id);
    await page.getByTestId("copy-start").click();
    await expect(form.getByTestId("form-name")).toBeVisible({ timeout: 60_000 });
    await expect(form.getByTestId("form-name")).toHaveValue(new RegExp(`^${scratchName} \\(`));
    await expect(form.getByTestId("copy-asked")).toContainText("return policy");  // no counterpart: asked, never guessed
    await group(page, "Shipping & policies");
    await expect(form.getByTestId("form-shipping_profile_id")).not.toHaveValue("");  // the same name: matched
    await expect(form.getByTestId("form-partner-note")).toContainText("has no production partner on Etsy. Drafts are made without one");
    await shot(page, "3-copy-form-empty-shop-partner-note", project);
    await form.getByTestId("form-return_policy_id").selectOption({ index: 1 });
    await form.getByTestId("profile-form-save").click();
    await expect(form).toBeHidden({ timeout: 30_000 });
    const copy = (await profiles(page)).find((p) => p.name.startsWith(`${scratchName} (`))!;
    made.push(copy.id);
    expect([copy.manual, copy.connection_id]).toEqual([true, empty.id]);

    // 4. A variation's price edited on the profile made by hand.
    await card.scrollIntoViewIfNeeded();
    await card.getByTestId("profile-edit").click();
    await expect(form.getByTestId("form-name")).toHaveValue(scratchName, { timeout: 60_000 });
    await group(page, "Price & variations");
    await form.getByTestId("variation-price").last().fill("27.25");
    await form.getByTestId("profile-form-save").click();
    await expect(form).toBeHidden({ timeout: 30_000 });
    const saved = await (await page.request.get(`/api/profiles/${scratch.id}/values`)).json();
    expect(saved.values.variations.rows.map((r: { price: number }) => r.price)).toEqual([24.5, 24.5, 27.25]);

    // 5. A profile that follows a listing: an edited field is "your value", and can follow the listing again.
    const follower = before.find((p) => !p.manual && p.confirmed && p.is_fresh);
    if (follower) {
      const theirs = page.locator(`#profile-${follower.id}`);
      await theirs.scrollIntoViewIfNeeded();
      await theirs.getByTestId("profile-edit").click();
      await expect(form.getByTestId("form-name")).toBeVisible({ timeout: 60_000 });
      await group(page, "Details");
      await form.getByTestId("form-materials").fill("Cotton, Linen blend");
      await form.getByTestId("profile-form-save").click();
      await expect(form).toBeHidden({ timeout: 30_000 });
      await expect(theirs.getByTestId("profile-own-count")).toHaveText("1 of your own value");
      await theirs.getByTestId("profile-edit").click();
      await group(page, "Details");
      await expect(form.getByTestId("own-materials")).toContainText("your value");
      await shot(page, "4-edit-form-your-value", project);
      await form.getByTestId("use-listing-materials").click();
      await expect(form.getByTestId("own-materials")).toHaveCount(0);
      await form.getByRole("button", { name: "Cancel" }).click();
      await expect(theirs.getByTestId("profile-own-count")).toHaveCount(0);
    }

    // 6. The new profile on a batch of the test's own (the demo's batches are left as they are), and a draft
    //    sent with it: one design uploaded, the profile chosen for it, a listing written by hand, approved, sent.
    const created = await write(page, "POST", "/api/batches", { connection_id: scratch.connection_id });
    expect(created.status, JSON.stringify(created.body)).toBe(201);
    const batch = created.body.id as string;
    batches.push(batch);
    const uploaded = await page.evaluate(async (id) => {
      const canvas = document.createElement("canvas");
      canvas.width = 900; canvas.height = 900;
      const ctx = canvas.getContext("2d")!;
      ctx.fillStyle = "#f4f1ea"; ctx.fillRect(0, 0, 900, 900);
      ctx.fillStyle = "#14162b"; ctx.font = "700 96px sans-serif"; ctx.fillText("Pine ridge", 180, 470);
      const blob: Blob = await new Promise((done) => canvas.toBlob((b) => done(b!), "image/png"));
      const form = new FormData();
      form.append("file", blob, "PRF100.png");
      form.append("grouping", "one");
      const res = await fetch(`/api/batches/${id}/assets`, { method: "POST", credentials: "include", body: form });
      return res.status;
    }, batch);
    expect(uploaded).toBe(201);
    expect((await write(page, "POST", `/api/batches/${batch}/finalize`, {})).status).toBe(200);
    // The worker prepares the photo; the batch is ready when no photo waits.
    await expect.poll(async () => {
      const detail = await (await page.request.get(`/api/batches/${batch}`)).json();
      const assets = (detail.assets ?? []) as { status: string }[];
      return assets.length > 0 && assets.every((a) => a.status === "processed");
    }, { timeout: 90_000, intervals: [1000] }).toBe(true);
    const assigned = await write(page, "PUT", `/api/batches/${batch}/groups`, { profile_id: scratch.id });
    expect(assigned.status, JSON.stringify(assigned.body)).toBe(200);
    expect((assigned.body as { profile_id: string | null }[]).every((g) => g.profile_id === scratch.id)).toBe(true);
    const written = await write(page, "POST", `/api/batches/${batch}/manual`, {});
    expect(written.status, JSON.stringify(written.body)).toBe(200);
    const listing = ((await (await page.request.get(`/api/batches/${batch}/content`)).json()) as { id: string; tags: string[] }[])[0];
    // The profile's default tags start the listing written with it.
    expect(listing.tags).toContain("soft tee");
    const text = await write(page, "PATCH", `/api/content/${listing.id}`, { title: "Pine Ridge Camping Shirt for Hikers" });
    expect(text.status, JSON.stringify(text.body)).toBe(200);
    const approved = await write(page, "POST", `/api/content/${listing.id}/approve`, { approved: true, connection_id: null });
    expect(approved.status, JSON.stringify(approved.body)).toBe(200);
    const draft = await write(page, "POST", `/api/batches/${batch}/send`, { when: "drafts" });
    expect(draft.status, JSON.stringify(draft.body)).toBe(200);
    expect(draft.body.jobs.length, JSON.stringify(draft.body)).toBe(1);
    // The batch page says at once what became of it: being made, made, or (no Etsy on this machine) why not.
    await page.goto(`/batches/${batch}?shop=${scratch.connection_id}`);
    const workspace = page.getByTestId("shop-workspace");
    await expect(workspace).toBeVisible({ timeout: 60_000 });
    await expect(workspace.getByTestId("workspace-row-trying").or(workspace.getByTestId("workspace-row-failed"))
      .or(workspace.getByText("drafted").first()).first()).toBeVisible({ timeout: 60_000 });
    await shot(page, "5-batch-draft-sent", project);
    expect(errors, "no error on any page of the flow").toEqual([]);
  });
});
