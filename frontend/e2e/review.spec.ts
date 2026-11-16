/**
 * The review and send flow, as it was on 13 November (31fa2f5): the card's own Approve, "Approve all" and the
 * one "Where and when" panel on the page (no bar, no "More" menu). With it, what was fixed on 16 November
 * and kept: a section choice that sticks, the reason on the card of a listing that cannot be approved, and
 * "Keep it, I know the risk" for a brand name.
 *
 * One walk, with real clicks: approve, send to one shop, send to two shops, a section saved.
 *
 * Needs a running server and the demo admin (`seed-demo --admin`): LAYOUT_EMAIL, LAYOUT_PASSWORD. The local
 * stack sends nothing to Etsy (ETSY_CALLS_BLOCKED): "Create drafts" queues the draft jobs, which is what is
 * checked here; what a draft job does is tested in the backend.
 */
import { expect, test, type Page } from "@playwright/test";
import { signIn } from "./session";

const EMAIL = process.env.LAYOUT_EMAIL;
const PASSWORD = process.env.LAYOUT_PASSWORD;
const BATCH = "Trip tees";

async function batchId(page: Page): Promise<string> {
  const answer = await page.request.get("/api/batches");
  expect(answer.status(), "the list of batches").toBe(200);
  const batch = ((await answer.json()) as { id: string; name: string }[]).find((b) => b.name === BATCH);
  expect(batch, "the demo has no Trip tees batch: seed-demo again").toBeTruthy();
  return batch!.id;
}

/** As seeded: nothing approved, no brand kept, no section chosen, each listing going to its profile's shop. */
async function reset(page: Page, batch: string): Promise<void> {
  await page.evaluate(async (id) => {
    const call = (url: string, method = "GET", body?: unknown) =>
      fetch(url, { method, credentials: "include", headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
    await call(`/api/batches/${id}/send-to`, "PUT", { send_to: null });
    for (const c of await (await call(`/api/batches/${id}/content`)).json()) {
      if ((c.kept_brands ?? []).length) await call(`/api/content/${c.id}/brands/unkeep`, "POST");
      if (c.approved) await call(`/api/content/${c.id}/approve`, "POST", { approved: false });
      const options = await (await call(`/api/content/${c.id}/options`)).json();
      const mine = options.shops.filter((s: { source: string }) => s.source === "seller");
      if (mine.length) {
        await call(`/api/content/${c.id}/options`, "PUT", { sections: Object.fromEntries(mine.map((s: { connection_id: string }) => [s.connection_id, "default"])) });
      }
    }
  }, batch);
}

const card = (page: Page, i: number) => page.getByTestId("review-card").nth(i);
const approved = (page: Page) => page.getByText(/\d+ of 3 approved/).first();

/** "Send to": tick these shops (and only these), by their place in the list. */
async function sendTo(page: Page, shops: number[]): Promise<void> {
  const panel = page.getByTestId("where-and-when");
  const saved = page.waitForResponse((r) => r.url().includes("/send-to") && r.request().method() === "PUT");
  await panel.getByTestId("send-to").click();
  const boxes = page.getByTestId("send-to-shop");
  await expect(boxes.first()).toBeVisible();
  for (let i = 0; i < await boxes.count(); i += 1) {
    if ((await boxes.nth(i).isChecked()) !== shops.includes(i)) await boxes.nth(i).click();
  }
  expect((await saved).status()).toBe(200);
  await page.getByRole("button", { name: "Done", exact: true }).click();
}

/** Press the panel's one button and return what the server queued. */
async function send(page: Page): Promise<{ jobs: unknown[] }> {
  const button = page.getByTestId("where-and-when").getByTestId("send-button");
  await expect(button).toBeEnabled({ timeout: 15_000 });
  const sent = page.waitForResponse((r) => /\/send$/.test(new URL(r.url()).pathname) && r.request().method() === "POST");
  await button.click();
  const answer = await sent;
  expect(answer.status(), "the send was accepted").toBe(200);
  return answer.json();
}

test.describe("review and send, as on 13 November", () => {
  test.skip(!EMAIL || !PASSWORD, "set LAYOUT_EMAIL and LAYOUT_PASSWORD (the demo admin from `seed-demo --admin`) to run this");

  test("approve, send to one shop, send to two shops, and a section that stays saved", async ({ page }, info) => {
    test.skip(info.project.name !== "desktop-1366", "one walk, at 1366 px");
    test.setTimeout(180_000);
    await signIn(page, EMAIL!, PASSWORD!);
    const batch = await batchId(page);
    await reset(page, batch);
    await page.goto(`/batches/${batch}/review`);
    await expect(page.getByTestId("review-card")).toHaveCount(3, { timeout: 60_000 });

    // Friday's page: Approve all and Where and when are on the page; there is no bar and no More menu.
    await expect(page.getByTestId("review-bar")).toHaveCount(0);
    await expect(page.getByTestId("review-more")).toHaveCount(0);
    await expect(page.getByTestId("review-next")).toContainText("Approve all (3)");
    const panel = page.getByTestId("where-and-when");
    await expect(panel).toBeVisible();
    await expect(approved(page)).toContainText("0 of 3 approved");

    // A section chosen on the card is saved with its own id, named as the seller's choice, and survives a reload.
    const select = card(page, 0).getByTestId("section-select").first();
    await expect(select).toBeVisible({ timeout: 30_000 });
    const saved = page.waitForResponse((r) => r.url().includes("/options") && r.request().method() === "PUT");
    await select.selectOption({ label: "Western Collection" });
    const put = await saved;
    expect(put.status()).toBe(200);
    expect(Object.values((put.request().postDataJSON() as { sections: Record<string, unknown> }).sections)).toEqual([41000003]);
    await expect(card(page, 0).getByTestId("section-why").first()).toHaveText("your choice · Western Collection");
    await page.reload();
    await expect(card(page, 0).getByTestId("section-select").first().locator("option:checked")).toHaveText("Western Collection", { timeout: 60_000 });
    await expect(card(page, 0).getByTestId("section-why").first()).toHaveText("your choice · Western Collection");
    await expect(page.getByText("no section, as you chose")).toHaveCount(0);

    // The card's own Approve, where it was on Friday; the count follows at once.
    for (const i of [0, 1]) {
      await card(page, i).getByTestId("approve-toggle").click();
      await expect(card(page, i).getByTestId("approve-toggle")).toHaveAttribute("aria-pressed", "true");
    }
    await expect(approved(page)).toContainText("2 of 3 approved");

    // A listing a brand name stops says why beside its Approve; "Keep it, I know the risk" approves it as written.
    await card(page, 2).getByTestId("approve-toggle").click();
    const blocked = card(page, 2).getByTestId("card-blocked");
    await expect(blocked).toContainText("Not approved: Remove: Mickey (title)");
    await blocked.getByTestId("card-blocked-keep").click();
    await expect(card(page, 2).getByTestId("approve-toggle")).toHaveAttribute("aria-pressed", "true");
    await expect(card(page, 2).getByTestId("card-kept-brand")).toContainText("Brand name kept by you: Mickey");
    await expect(card(page, 2).locator('[data-field="title"]')).toHaveValue("Mickey Ears Family Vacation Shirt");
    await expect(approved(page)).toContainText("3 of 3 approved");

    // Send to ONE shop: the sentence and the button say so, and the drafts are queued.
    await sendTo(page, [0]);
    await expect(panel.getByTestId("send-sentence")).toContainText("3 listings → 1 shop: 3 drafts", { timeout: 15_000 });
    await expect(panel.getByTestId("send-button")).toHaveText("Create 3 drafts");
    expect((await send(page)).jobs.length).toBe(3);

    // Send to TWO shops: every approved listing goes to both.
    await sendTo(page, [0, 1]);
    await expect(panel.getByTestId("send-sentence")).toContainText("→ 2 shops", { timeout: 15_000 });
    await expect(panel.getByTestId("send-button")).toHaveText(/^Create \d+ drafts?$/);
    expect((await send(page)).jobs.length).toBeGreaterThanOrEqual(3);

    await reset(page, batch);
  });

  test("a batch uploaded for one shop goes to that shop: Send to names it, then that shop and another", async ({ page }, info) => {
    test.skip(info.project.name !== "desktop-1366", "one walk, at 1366 px");
    test.setTimeout(240_000);
    await signIn(page, EMAIL!, PASSWORD!);
    // The upload, as the Upload page makes it while the seller works on a shop that is NOT the profile's
    // (the same requests: a batch for that shop, one photo, written by hand with a profile of another shop).
    const made = await page.evaluate(async () => {
      const call = async (url: string, method = "GET", body?: unknown) => {
        const r = await fetch(url, { method, credentials: "include", headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
        return { status: r.status, json: await r.json().catch(() => null) };
      };
      const shops = (await call("/api/shops")).json.shops as { id: string; name: string }[];
      const profiles = (await call("/api/profiles")).json as { id: string; connection_id: string; confirmed: boolean; name: string }[];
      const profile = profiles.find((p) => p.connection_id === shops[0].id && p.confirmed !== false)!;
      const worked = shops.find((s) => s.id !== profile.connection_id)!;
      const batch = (await call("/api/batches", "POST", { connection_id: worked.id })).json.id as string;
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = 1200;
      const ctx = canvas.getContext("2d")!;
      ctx.fillStyle = "#14162B";
      ctx.fillRect(0, 0, 1200, 1200);
      ctx.fillStyle = "#FFD24A";
      ctx.fillRect(300, 300, 600, 600);
      const blob: Blob = await new Promise((done) => canvas.toBlob((b) => done(b!), "image/png"));
      const form = new FormData();
      form.append("file", blob, "HOME901.png");
      form.append("group_key", "HOME901");
      const up = await fetch(`/api/batches/${batch}/assets`, { method: "POST", credentials: "include", body: form });
      await call(`/api/batches/${batch}/finalize`, "POST");
      await call(`/api/batches/${batch}/groups`, "PUT", { group_keys: ["HOME901"], profile_id: profile.id });
      let written = { status: 0, json: null as unknown };
      let content: { id: string } | undefined;
      for (let i = 0; i < 60 && !content; i += 1) {  // the worker prepares the photo first; until then nothing is written
        written = await call(`/api/batches/${batch}/manual`, "POST", {});
        content = ((await call(`/api/batches/${batch}/content`)).json as { id: string }[] | null)?.[0];
        if (!content) await new Promise((r) => setTimeout(r, 1000));
      }
      if (!content) return { batch, worked, profileShop: shops[0], upload: up.status, written: written.status, text: 0, order: shops.map((s) => s.id) };
      const text = await call(`/api/content/${content.id}`, "PATCH", {
        title: "Cozy Home Graphic Tee, Yellow Square Shirt",
        tags: ["home shirt", "cozy tee", "graphic tee", "yellow shirt", "square tee", "soft shirt", "gift tee", "unisex tee",
               "casual shirt", "cotton tee", "simple shirt", "minimal tee", "weekend tee"],
      });
      return { batch, worked, profileShop: shops[0], upload: up.status, written: written.status, text: text.status,
               order: shops.map((s) => s.id) };
    });
    expect([made.upload, made.written, made.text], "uploaded, written by hand, titled").toEqual([201, 200, 200]);

    await page.goto(`/batches/${made.batch}/review`);
    await expect(page.getByTestId("review-card")).toHaveCount(1, { timeout: 60_000 });
    const panel = page.getByTestId("where-and-when");
    // Before anything is sent the panel names the shop the batch was uploaded for, not the profile's shop.
    await expect(panel.getByTestId("send-to")).toHaveAttribute("aria-label", `Send to: ${made.worked.name}`, { timeout: 15_000 });
    await expect(panel.getByTestId("send-to")).not.toContainText(made.profileShop.name);
    await card(page, 0).getByTestId("approve-toggle").click();
    await expect(card(page, 0).getByTestId("approve-toggle")).toHaveAttribute("aria-pressed", "true");
    await expect(panel.getByTestId("send-sentence")).toContainText("1 listing → 1 shop: 1 draft", { timeout: 15_000 });
    // The card says whose profile it is and where the listing goes.
    await expect(card(page, 0).getByTestId("card-profile-shop")).toContainText(
      `Profile is from ${made.profileShop.name}; this listing goes to ${made.worked.name}.`);
    expect((await send(page)).jobs.length).toBe(1);

    // That shop and another: one visible click on "Send to", on the page.
    await sendTo(page, [made.order.indexOf(made.worked.id), made.order.indexOf(made.profileShop.id)]);
    await expect(panel.getByTestId("send-sentence")).toContainText("→ 2 shops", { timeout: 15_000 });
    expect((await send(page)).jobs.length).toBeGreaterThanOrEqual(1);

    await page.evaluate((id) => fetch(`/api/batches/${id}`, { method: "DELETE", credentials: "include" }), made.batch);
  });
});
