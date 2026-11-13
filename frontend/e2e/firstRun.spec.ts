import { mkdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { expect, test, type Page } from "@playwright/test";

/**
 * A new seller's first run, in a real browser, on the shop-connection stack (docker-compose.oauth-e2e.yml:
 * the production frontend image, the API on its own host, a stub "Etsy" page; synthetic accounts only,
 * nothing goes to Etsy). At 1366 px and on a 375 px phone:
 *
 *   sign up with an invite → Home shows the checklist, step 1 next → every empty screen says one
 *   sentence and offers one button → connect the shop → land on "Upload your first design" →
 *   try the example designs → approve one (key A on a desktop, the button on a phone) → Undo →
 *   approve again → select the other two and "Approve selected" → upload a design of their own →
 *   Home: steps 1 and 2 ticked.
 *
 * The time from pressing "Create account" to the first approval is measured and written to
 * test-results/first-run-<size>.json (and printed). The examples make no model call, so no AI is
 * stubbed: the review screen shows the result our AI wrote once (backend/app/examples/examples.json).
 */
const APP = "http://listyro.test:3811";
const STUB_HOST = "etsy-stub.test";
const PASSWORD = "first-run-e2e-password-long";
const ACCOUNTS: Record<string, { email: string; code: string }> = {
  "desktop-1366": { email: "first-run-desktop@example.com", code: "first-run-desktop-e2e-code" },
  "phone-375": { email: "first-run-phone@example.com", code: "first-run-phone-e2e-code" },
};
const DESIGN = resolve(process.cwd(), "..", "backend", "app", "examples", "tomato-season.png");

test.describe("a new seller's first run", () => {
  test.beforeEach(({}, info) => {
    test.skip(!ACCOUNTS[info.project.name], "desktop and phone");
    test.skip(process.env.OAUTH_E2E === "off", "OAUTH_E2E=off: the stack was not started");
  });

  test("sign up → connect → examples → approve, undo, approve selected → first upload", async ({ browser }, info) => {
    test.setTimeout(240_000);
    const phone = info.project.name === "phone-375";
    const { email, code } = ACCOUNTS[info.project.name];
    const use = info.project.use;
    const context = await browser.newContext({ viewport: use.viewport, isMobile: use.isMobile, hasTouch: use.hasTouch,
      deviceScaleFactor: use.deviceScaleFactor });
    // Our pages and the stub only: nothing ever goes to Etsy from a browser (CLAUDE.md rule 1).
    await context.route(/^https?:\/\/([^/]*\.)?etsy\.com\//, (route) => route.abort());
    const page = await context.newPage();
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));

    // --- Sign up ---------------------------------------------------------------------------------
    await page.goto(`${APP}/register?invite=${code}&email=${encodeURIComponent(email)}`);
    await page.waitForLoadState("networkidle");
    await page.locator("#password").fill(PASSWORD);
    await page.getByTestId("consent-checkbox").check();
    const started = Date.now();
    await page.locator('button[type="submit"]').click();
    await page.waitForURL(/\/dashboard/, { timeout: 30_000 });

    // --- Home: the checklist, and its next step ----------------------------------------------------
    const checklist = page.getByTestId("setup-checklist");
    await expect(checklist).toBeVisible();
    await expect(page.getByTestId("step-shop")).toHaveAttribute("data-state", "now");
    await expect(page.getByTestId("step-upload")).toHaveAttribute("data-state", "later");
    await expect(page.locator("h1")).toHaveText("Home");
    // A new account's Home is the checklist, not cards of zeros.
    await expect(page.getByTestId("needs-attention")).toHaveCount(0);

    // --- Empty screens: one sentence, one button -----------------------------------------------------
    for (const [path, testId, button] of [
      ["/batches", "designs-empty", "Upload designs"],
      ["/review", "review-empty", "Upload designs"],
      ["/scheduled", "schedule-empty", "Review listings"],
      ["/profiles", "needs-shop", "Connect your shop"],
      ["/analytics", "needs-shop", "Connect your shop"],
    ] as const) {
      await page.goto(`${APP}${path}`);
      const empty = page.getByTestId(testId);
      await expect(empty, path).toBeVisible();
      await expect(empty.locator("p")).toHaveCount(1);
      await expect(empty.locator(".btn-primary")).toHaveText(button);
    }

    // --- Connect the shop: back on "Upload your first design" -------------------------------------------
    await page.goto(`${APP}/dashboard`);
    await page.getByTestId("checklist-next").click();
    await page.waitForURL(/\/connect/);
    await page.locator('a[href="/api/auth/etsy/start"]').first().click();
    await page.waitForURL((u) => u.hostname === STUB_HOST, { timeout: 20_000 });
    await page.locator("#allow-own-shop").click(); // the stub's "another seller": a shop of their own
    await page.waitForURL((u) => u.pathname === "/upload" && u.searchParams.has("connected"), { timeout: 30_000 });
    await expect(page.getByTestId("shop-connected")).toContainText("Your shop is connected");
    await expect(page.getByRole("heading", { name: "Upload your first design" })).toBeVisible();

    // --- The examples: written once by our AI, labelled, never sent ---------------------------------------
    await page.getByTestId("try-examples").click();
    await page.waitForURL(/\/batches\/[^/]+\/review$/, { timeout: 60_000 });
    await expect(page.getByTestId("example-banner")).toBeVisible();
    const cards = page.getByTestId("review-card");
    await expect(cards).toHaveCount(3);
    await expect(page.getByTestId("example-label").first()).toBeVisible();
    await expect(page.getByTestId("live-checks").first()).toBeVisible();
    await expect(page.locator("#send")).toHaveCount(0); // no "Where and when" for examples
    await expect(page.getByTestId("example-not-sent").first()).toBeVisible();

    // --- First approval: key A on a desktop (J moves to the first card), the button on a phone -----------
    const first = cards.nth(0);
    if (phone) {
      await first.getByTestId("approve-toggle").click();
    } else {
      await page.keyboard.press("j");
      await expect(page.locator('[data-card-index="0"]')).toHaveClass(/ring-2/);
      await page.keyboard.press("a");
    }
    await expect(first).toHaveAttribute("data-approved", "true");
    const seconds = (Date.now() - started) / 1000;
    const toast = page.getByTestId("toast").filter({ hasText: "Approved" });
    await expect(toast).toBeVisible();

    // --- Undo: the approval is taken back, then given again -------------------------------------------------
    await toast.getByRole("button", { name: "Undo" }).click();
    await expect(first).toHaveAttribute("data-approved", "false");
    await first.getByTestId("approve-toggle").click();
    await expect(first).toHaveAttribute("data-approved", "true");

    if (!phone) {
      // --- Keyboard: ? lists the keys, Esc closes; E goes to the title; typing there never approves ------
      await page.keyboard.press("?");
      await expect(page.getByTestId("keys-help")).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(page.getByTestId("keys-help")).toHaveCount(0);
      await page.keyboard.press("j");
      await page.keyboard.press("e");
      await expect(cards.nth(1).locator('[data-field="title"]')).toBeFocused();
      await page.keyboard.press("a"); // typed into the title, not an approval
      await expect(cards.nth(1)).toHaveAttribute("data-approved", "false");
      await page.keyboard.press("Backspace");
      await page.keyboard.press("Escape");
      await page.keyboard.press("t");
      await expect(cards.nth(1).locator('[data-field="tags"] [data-tag-input]')).toBeFocused();
      await page.keyboard.press("Escape");
    }

    // --- Bulk approve: select the other two, "Approve selected" -------------------------------------------------
    await page.getByTestId("select-toggle").click(); // Part H: one "Select" toggle, a checkbox per card
    await cards.nth(1).getByTestId("select-listing").check();
    await cards.nth(2).getByTestId("select-listing").check();
    await expect(page.getByTestId("review-next")).toHaveText("Approve selected (2)");
    await page.getByTestId("review-next").click();
    await expect(page.getByTestId("toast").filter({ hasText: "Approved 2" })).toBeVisible();
    await expect(cards.nth(1)).toHaveAttribute("data-approved", "true");
    await expect(cards.nth(2)).toHaveAttribute("data-approved", "true");

    // --- A design of their own: step 2 ticks itself -------------------------------------------------------------
    await page.goto(`${APP}/upload`);
    await page.getByTestId("files-input").setInputFiles(DESIGN);
    await page.getByTestId("upload-start").click();
    await expect(page.getByTestId("upload-continue")).toBeVisible({ timeout: 60_000 });
    await page.goto(`${APP}/dashboard`);
    await expect(page.getByTestId("step-shop")).toHaveAttribute("data-state", "done");
    await expect(page.getByTestId("step-upload")).toHaveAttribute("data-state", "done");
    // The examples were approved to learn the screen; that is not the seller's own first approval.
    // Step 2, the listing settings, is next: this stub shop has no listings to suggest them from
    // (Part E), so the step says it is reading the shop and links to Profiles.
    await expect(page.getByTestId("step-profile")).toHaveAttribute("data-state", "now");
    await expect(page.getByTestId("step-approve")).toHaveAttribute("data-state", "later");

    expect(errors, "no JavaScript error on the way").toEqual([]);
    const out = join(process.cwd(), "test-results");
    mkdirSync(out, { recursive: true });
    writeFileSync(join(out, `first-run-${info.project.name}.json`), JSON.stringify({ size: info.project.name, seconds_to_first_approval: seconds }, null, 2));
    console.log(`first run, ${info.project.name}: sign-up to first approval ${seconds.toFixed(1)} s`);
    await context.close();
  });
});
