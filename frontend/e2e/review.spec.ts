/**
 * The review page's two regressions of 14 November 2026 (hotfix/review-regressions), in a real browser at
 * 375, 768 and 1366 px, on the synthetic demo's "Trip tees" batch (backend/scripts/demo_screens_seed.py):
 * three listings nobody approved, in a shop with sections where no section fits any of them clearly; one
 * names a brand in a form the filter lets through (a warning: it can be approved), one names a brand the
 * account's filter stops (it cannot, and its card says why).
 *
 *   1. A section chosen on a card is saved, is still chosen after a reload, says "your choice · <section>",
 *      is never "no section, as you chose" unless "No section" was picked, and a save that fails says so.
 *   3. A listing a brand name stops (the account's filter, as on 13 November): the reason on its card, and
 *      beside it "Keep it, I know the risk": one click records the seller's decision for that listing and
 *      name and approves it. The warning stays on the card; the title is never edited.
 *   2. Approve: each card's own button, the bar's "Approve this listing", "Approve N selected" and
 *      "Approve all N waiting" (with its confirmation) all approve what they say, the counts follow at once,
 *      and a listing that cannot be approved says why on its card.
 *
 * Real clicks only: nothing is forced. Each test puts the batch back as it was seeded, before and after.
 * How to run: playwright.config.ts (the signed-in half needs LAYOUT_EMAIL and LAYOUT_PASSWORD).
 */
import { expect, test, type Page } from "@playwright/test";
import { signIn } from "./session";

const EMAIL = process.env.LAYOUT_EMAIL;
const PASSWORD = process.env.LAYOUT_PASSWORD;
const BATCH = "Trip tees";

/** The batch's address ("/batches/<id>"). Asked of the API once a run: opening the Designs page before and
 *  after every test reads every batch of the demo, and the run then meets the API's own request limit. */
let found: string | null = null;
async function batchHref(page: Page): Promise<string> {
  if (found) return found;
  const answer = await page.request.get("/api/batches");
  expect(answer.status(), "the list of batches").toBe(200);
  const batch = ((await answer.json()) as { id: string; name: string }[]).find((b) => b.name === BATCH);
  expect(batch, "the demo has no Trip tees batch: seed-demo again").toBeTruthy();
  found = `/batches/${batch!.id}`;
  return found;
}

/** As seeded: nothing approved, no section chosen, each listing going to its profile's shop. */
async function reset(page: Page, href: string): Promise<void> {
  await page.evaluate(async (batch) => {
    const call = (url: string, method = "GET", body?: unknown) =>
      fetch(url, { method, credentials: "include", headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
    await call(`/api/batches/${batch}/send-to`, "PUT", { send_to: null });
    for (const c of await (await call(`/api/batches/${batch}/content`)).json()) {
      if ((c.kept_brands ?? []).length) await call(`/api/content/${c.id}/brands/unkeep`, "POST");
      if (c.approved) await call(`/api/content/${c.id}/approve`, "POST", { approved: false });
      const options = await (await call(`/api/content/${c.id}/options`)).json();
      const mine = options.shops.filter((s: { source: string }) => s.source === "seller");
      if (mine.length) {
        await call(`/api/content/${c.id}/options`, "PUT", { sections: Object.fromEntries(mine.map((s: { connection_id: string }) => [s.connection_id, "default"])) });
      }
    }
  }, href.split("/").pop()!);
}

async function openReview(page: Page): Promise<string> {
  const href = await batchHref(page);
  await reset(page, href);
  await page.goto(`${href}/review`);
  await expect(page.getByTestId("review-bar")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("review-card")).toHaveCount(3);
  await expect(page.getByTestId("list-header")).toContainText("0 of 3 approved");
  return href;
}

const card = (page: Page, i: number) => page.locator(`[data-card-index="${i}"]`);
const select = (page: Page, i: number) => card(page, i).getByTestId("section-select").first();
const why = (page: Page, i: number) => card(page, i).getByTestId("section-why").first();

/** The card at the top of the screen, as a seller scrolls to it. */
async function bringUp(page: Page, i: number): Promise<void> {
  await card(page, i).evaluate((el) => el.scrollIntoView({ block: "start" }));
}

test.describe("review: a section that sticks, an Approve that approves", () => {
  test.skip(!EMAIL || !PASSWORD, "set LAYOUT_EMAIL and LAYOUT_PASSWORD (the demo admin from `seed-demo --admin`) to run these");

  // The API allows 1,200 requests a minute from one address (core/ratelimit.py). Each of these tests opens
  // the dashboard and the review page (about 80 requests) and takes about three seconds against the
  // production image: back to back they pass that limit and the API answers 429. So a test starts no
  // sooner than six seconds after the one before it.
  let began = 0;
  test.beforeEach(async ({ page }) => {
    const wait = began + 6_000 - Date.now();
    if (wait > 0) await page.waitForTimeout(wait);
    began = Date.now();
    await signIn(page, EMAIL!, PASSWORD!);
  });
  test.afterEach(async ({ page }) => {
    const href = await batchHref(page).catch(() => null);
    if (href) await reset(page, href);
  });

  test("a section chosen on the card is saved, named as the seller's choice, and still chosen after a reload", async ({ page }) => {
    await openReview(page);
    await expect(select(page, 0)).toBeVisible({ timeout: 30_000 });
    // The suggestion: no section fits this design clearly.
    await expect(select(page, 0)).toHaveValue("none");
    await expect(why(page, 0)).toHaveText("no section fits clearly");
    const saved = page.waitForResponse((r) => r.url().includes("/options") && r.request().method() === "PUT");
    await select(page, 0).selectOption({ label: "Western Collection" });
    const sent = (await saved).request().postDataJSON() as { sections: Record<string, unknown> };
    expect(Object.values(sent.sections), "the section's own id is sent, never null").toEqual([41000003]);
    await expect(why(page, 0)).toHaveText("your choice · Western Collection");
    await expect(select(page, 0).locator("option:checked")).toHaveText("Western Collection");
    await expect(card(page, 0).getByTestId("section-not-saved")).toHaveCount(0);
    // A reload: the same.
    await page.reload();
    await expect(select(page, 0)).toBeVisible({ timeout: 30_000 });
    await expect(select(page, 0).locator("option:checked")).toHaveText("Western Collection");
    await expect(why(page, 0)).toHaveText("your choice · Western Collection");
    await expect(card(page, 0).getByText("no section, as you chose")).toHaveCount(0);
    // "No section, as you chose" is said only when the seller picks "No section".
    await select(page, 0).selectOption({ label: "No section" });
    await expect(why(page, 0)).toHaveText("your choice · no section, as you chose");
    await page.reload();
    await expect(why(page, 0)).toHaveText("your choice · no section, as you chose", { timeout: 30_000 });
    // The other cards were never touched.
    await expect(why(page, 1)).toHaveText("no section fits clearly");
  });

  test("a section that could not be saved says so on the card, and Try again saves it", async ({ page }) => {
    await openReview(page);
    await expect(select(page, 1)).toBeVisible({ timeout: 30_000 });
    let failing = true;
    await page.route("**/api/content/*/options", async (route) => {
      if (failing && route.request().method() === "PUT") await route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "internal error" }) });
      else await route.continue();
    });
    await select(page, 1).selectOption({ label: "Western Collection" });
    const note = card(page, 1).getByTestId("section-not-saved");
    await expect(note).toContainText("Couldn't save the section. Try again.");
    // Nothing pretends to be saved: the control is back on what the server has, and says whose that is.
    await expect(select(page, 1)).toHaveValue("none");
    await expect(why(page, 1)).toHaveText("no section fits clearly");
    failing = false;
    await note.getByRole("button", { name: "Try again" }).click();
    await expect(why(page, 1)).toHaveText("your choice · Western Collection");
    await expect(note).toHaveCount(0);
  });

  test("a background read that answers late never puts the old section back", async ({ page }) => {
    const href = await openReview(page);
    // Sent to a group whose other shops' sections are not read yet: the card looks again every few seconds.
    const groups = await page.evaluate(async (b) => (await (await fetch(`/api/batches/${b}/send`, { credentials: "include" })).json()).groups ?? [], href.split("/").pop()!);
    test.skip(groups.length === 0, "one shop: nothing is read in the background");
    await page.evaluate(async ([b, g]) => {
      await fetch(`/api/batches/${b}/send-to`, { method: "PUT", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ send_to: { group_ids: [g], connection_ids: [] } }) });
    }, [href.split("/").pop()!, groups[0].id]);
    await page.reload();
    await expect(select(page, 0)).toBeVisible({ timeout: 30_000 });
    await expect(card(page, 0).getByText("Reading this shop's sections…").first()).toBeVisible();
    // One background read of card 0 is held back, answered with what the server said before the pick.
    let release!: () => void;
    const released = new Promise<void>((done) => { release = done; });
    let caught!: () => void;
    const held = new Promise<void>((done) => { caught = done; });
    let holding = true;
    const first = await card(page, 0).getByTestId("review-card").getAttribute("id");
    await page.route(`**/api/content/${first}/options`, async (route) => {
      if (!holding || route.request().method() !== "GET") return route.continue();
      holding = false;
      const stale = await route.fetch();
      caught();
      await released;
      await route.fulfill({ response: stale });
    });
    await held; // the stale read is on its way
    await select(page, 0).selectOption({ label: "Western Collection" });
    await expect(why(page, 0)).toHaveText("your choice · Western Collection");
    release();
    await page.waitForTimeout(1200);
    await expect(why(page, 0)).toHaveText("your choice · Western Collection");
    await expect(select(page, 0).locator("option:checked")).toHaveText("Western Collection");
  });

  test("every card has its own Approve where it begins, and it approves that listing", async ({ page }) => {
    await openReview(page);
    for (const i of [0, 1, 2]) {
      await bringUp(page, i);
      const button = card(page, i).getByTestId("approve-top");
      await expect(button, `card ${i}: its Approve is on screen when the card is`).toBeInViewport();
      await expect(button).toHaveText("Approve");
      await expect(button).toBeEnabled();
    }
    await bringUp(page, 0);
    await card(page, 0).getByTestId("approve-top").click();
    await expect(card(page, 0).getByTestId("review-card")).toHaveAttribute("data-approved", "true");
    await expect(page.getByTestId("list-header")).toContainText("1 of 3 approved");
    await expect(page.getByTestId("review-summary")).toContainText("2 more are waiting for your approval.");
    await expect(card(page, 0).getByTestId("approve-top")).toHaveText("Take approval back");
    // The one at the card's foot is the same button.
    await card(page, 1).getByTestId("approve-toggle").scrollIntoViewIfNeeded();
    await card(page, 1).getByTestId("approve-toggle").click();
    await expect(page.getByTestId("list-header")).toContainText("2 of 3 approved");
  });

  test("the bar's Approve approves the listing on screen, says which, and the counts follow at once", async ({ page }) => {
    await openReview(page);
    await expect(select(page, 0)).toBeVisible({ timeout: 30_000 });
    // A listing with a section set, one with a brand warning, one the filter stops.
    await select(page, 0).selectOption({ label: "Western Collection" });
    await expect(why(page, 0)).toHaveText("your choice · Western Collection");
    await expect(card(page, 1).getByTestId("brand-warnings")).toContainText("Disney");
    await bringUp(page, 0);
    const next = page.getByTestId("review-next");
    await expect(next).toHaveText("Approve this listing");
    await expect(page.getByTestId("review-current")).toContainText("1 of 3: TRIP701");
    await next.click();
    await expect(page.getByTestId("list-header")).toContainText("1 of 3 approved");
    await expect(page.getByTestId("review-note")).toHaveText("Approved. 2 waiting.");
    await expect(card(page, 0).getByTestId("review-card")).toHaveAttribute("data-approved", "true");
    // The "Approved · Undo" note never lies over the bar's buttons (below 1280 px the bar is at the foot
    // of the screen, where the notes appear): the next click on Approve is a click on Approve.
    const toast = page.getByTestId("toast").filter({ hasText: "Approved" }).first();
    await expect(toast).toBeVisible();
    if (page.viewportSize()!.width < 1280) {
      const [note, bar] = [(await toast.boundingBox())!, (await page.getByTestId("review-bar").boundingBox())!];
      expect(note.y + note.height, "the note stays above the bar").toBeLessThanOrEqual(bar.y + 1);
    }
    // The next one that waits comes up: the brand warning is a warning, the listing is approved.
    await expect(page.getByTestId("review-current")).toContainText("2 of 3: TRIP702");
    await expect(next).toHaveText("Approve this listing");
    await next.click();
    await expect(page.getByTestId("list-header")).toContainText("2 of 3 approved");
    await expect(page.getByTestId("review-note")).toHaveText("Approved. 1 waiting.");
    // The third names a brand the account's filter stops: not approved, and its card says why, with the fix.
    await expect(page.getByTestId("review-current")).toContainText("3 of 3: TRIP703");
    await next.click();
    const blocked = card(page, 2).getByTestId("card-blocked");
    await expect(blocked).toBeVisible();
    await expect(blocked).toContainText("Not approved: Remove: Mickey (title)");
    await expect(blocked).toBeInViewport();
    await expect(page.getByTestId("review-note")).toHaveText("1 listing can't be approved yet: its card says why. 1 waiting.");
    await expect(page.getByTestId("list-header")).toContainText("2 of 3 approved");
    await blocked.getByRole("button", { name: "Fix the title" }).click();
    await expect(card(page, 2).locator('[data-field="title"]')).toBeFocused();
    // Approved listings can be sent while one still waits.
    await expect(page.getByTestId("review-send")).toHaveText("Send (2)");
  });

  test("Approve all N waiting asks first, approves what can be, and says why on the one that cannot", async ({ page }) => {
    await openReview(page);
    await page.getByTestId("review-more").locator("summary").click();
    const all = page.getByTestId("approve-all");
    await expect(all).toHaveText("Approve all 3 waiting");
    await all.click();
    const ask = page.getByTestId("approve-all-ask");
    await expect(ask).toContainText("Approve all 3 listings that are waiting?");
    // Nothing is approved until it is confirmed.
    await expect(page.getByTestId("list-header")).toContainText("0 of 3 approved");
    await ask.getByRole("button", { name: "Cancel" }).click();
    await expect(ask).toHaveCount(0);
    await expect(page.getByTestId("list-header")).toContainText("0 of 3 approved");
    await page.getByTestId("review-more").locator("summary").click();
    await page.getByTestId("approve-all").click();
    await page.getByTestId("approve-all-confirm").click();
    await expect(page.getByTestId("list-header")).toContainText("2 of 3 approved");
    await expect(page.getByTestId("review-note")).toHaveText("Approved 2. 1 listing can't be approved yet: its card says why. 1 waiting.");
    await expect(card(page, 2).getByTestId("card-blocked")).toContainText("Not approved: Remove: Mickey (title)");
    await expect(card(page, 0).getByTestId("card-blocked")).toHaveCount(0);
    await page.getByTestId("review-more").locator("summary").click();
    await expect(page.getByTestId("approve-all")).toHaveText("Approve all 1 waiting");
  });

  test("Approve N selected approves the ticked listings", async ({ page }) => {
    await openReview(page);
    await page.getByTestId("select-toggle").click();
    await card(page, 0).getByTestId("select-listing").check();
    await card(page, 1).getByTestId("select-listing").check();
    const next = page.getByTestId("review-next");
    await expect(next).toHaveText("Approve 2 selected");
    await next.click();
    await expect(page.getByTestId("list-header")).toContainText("2 of 3 approved");
    await expect(page.getByTestId("review-note")).toHaveText("Approved 2. 1 waiting.");
    await expect(card(page, 0).getByTestId("review-card")).toHaveAttribute("data-approved", "true");
    await expect(card(page, 1).getByTestId("review-card")).toHaveAttribute("data-approved", "true");
    await expect(card(page, 2).getByTestId("review-card")).toHaveAttribute("data-approved", "false");
  });

  test("a Mickey listing: the reason is shown, Keep it approves it, and the warning stays", async ({ page }) => {
    await openReview(page);
    await bringUp(page, 2);
    const title = card(page, 2).locator('[data-field="title"]');
    await expect(title).toHaveValue("Mickey Ears Family Vacation Shirt");
    // The bar's Approve on it: not approved, and the card says why.
    await expect(page.getByTestId("review-current")).toContainText("3 of 3: TRIP703");
    await page.getByTestId("review-next").click();
    const blocked = card(page, 2).getByTestId("card-blocked");
    await expect(blocked).toContainText("Not approved: Remove: Mickey (title)");
    await expect(page.getByTestId("list-header")).toContainText("0 of 3 approved");
    // Right beside the reason: the seller's own decision, one click.
    const keep = blocked.getByTestId("card-blocked-keep");
    await expect(keep).toHaveText("Keep it, I know the risk");
    await expect(keep).toBeInViewport();
    const saved = page.waitForResponse((r) => r.url().includes("/brands/keep") && r.request().method() === "POST");
    await keep.click();
    expect((await saved).status()).toBe(200);
    await expect(card(page, 2).getByTestId("review-card")).toHaveAttribute("data-approved", "true");
    await expect(page.getByTestId("list-header")).toContainText("1 of 3 approved");
    await expect(blocked).toHaveCount(0);
    // The warning is not hidden, the decision is said, and the title is as the seller wrote it.
    await expect(card(page, 2).getByTestId("brand-warnings")).toContainText("Mickey is a trademark.");
    await expect(card(page, 2).getByTestId("card-kept-brand")).toContainText("Brand name kept by you: Mickey");
    await expect(title).toHaveValue("Mickey Ears Family Vacation Shirt");
    // A reload: still approved, still kept, still warned.
    await page.reload();
    await expect(page.getByTestId("list-header")).toContainText("1 of 3 approved", { timeout: 30_000 });
    await expect(card(page, 2).getByTestId("review-card")).toHaveAttribute("data-approved", "true");
    await expect(card(page, 2).getByTestId("card-kept-brand")).toContainText("Brand name kept by you: Mickey");
    await expect(card(page, 2).getByTestId("brand-warnings")).toContainText("Mickey is a trademark.");
    // The other two were not approved by it, and a listing with only a warning is offered no "Keep it".
    await expect(card(page, 0).getByTestId("review-card")).toHaveAttribute("data-approved", "false");
    await expect(card(page, 1).getByTestId("card-blocked-keep")).toHaveCount(0);
  });

  test("a card's own Approve on a listing that cannot be approved says why beside the button", async ({ page }) => {
    await openReview(page);
    await bringUp(page, 2);
    await card(page, 2).getByTestId("approve-top").click();
    const blocked = card(page, 2).getByTestId("card-blocked");
    await expect(blocked).toContainText("Not approved: Remove: Mickey (title)");
    await expect(blocked).toBeInViewport();
    await expect(blocked.getByTestId("card-blocked-keep")).toHaveText("Keep it, I know the risk");
    await expect(card(page, 2).getByTestId("card-blocked-foot")).toContainText("Remove: Mickey (title)");
    await expect(card(page, 2).getByTestId("review-card")).toHaveAttribute("data-approved", "false");
    await expect(page.getByTestId("list-header")).toContainText("0 of 3 approved");
  });
});
