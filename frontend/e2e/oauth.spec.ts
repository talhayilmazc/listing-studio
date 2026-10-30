import { execFileSync } from "node:child_process";
import { expect, test, type Browser, type BrowserContext, type Page } from "@playwright/test";
import { COMPOSE } from "./oauth.global";

/**
 * Connecting a shop in a real browser, with production's hosts and cookie rules
 * (docker-compose.oauth-e2e.yml). The app is on listyro.test, Etsy sends the seller back to
 * api.listyro.test (like api.listyro.com) and "Etsy" is a stub page on a third origin. The
 * session cookie is host-only for the app, so it never reaches the callback: this is how every
 * connection failed in production from 2026-10-23 to 2026-10-29 while the API tests passed (their
 * client sent cookies a browser does not). The callback must connect on the binding cookie alone.
 */
const APP = "http://listyro.test:3811";
const API_HOST = "api.listyro.test";
const STUB_HOST = "etsy-stub.test";
const PASSWORD = "connect-a-shop-e2e";
const TOO_LONG = "The connection took too long or started in another browser. Press Connect a shop again.";

test.describe("connect a shop (two hosts, real cookies)", () => {
  test.beforeEach(({}, info) => {
    test.skip(info.project.name !== "desktop-1366", "one browser size is enough for this flow");
    test.skip(process.env.OAUTH_E2E === "off", "OAUTH_E2E=off: the shop-connection stack was not started");
  });

  async function signedIn(browser: Browser, email: string): Promise<{ context: BrowserContext; page: Page }> {
    const context = await browser.newContext();
    // Our pages and the stub only: nothing ever goes to Etsy from a browser (CLAUDE.md rule 1).
    await context.route(/^https?:\/\/([^/]*\.)?etsy\.com\//, (route) => route.abort());
    const page = await context.newPage();
    await page.goto(`${APP}/login`);
    await page.locator("#email").fill(email);
    await page.locator("#password").fill(PASSWORD);
    await page.locator('button[type="submit"]').click();
    await page.waitForURL(/\/dashboard/, { timeout: 30_000, waitUntil: "commit" });
    return { context, page };
  }

  /** Shops page → Connect a shop → the stub "Etsy authorize" page; returns its URL. */
  async function startConnecting(page: Page): Promise<URL> {
    await page.goto(`${APP}/connect`);
    await page.locator('a[href="/api/auth/etsy/start"]').first().click();
    await page.waitForURL((u) => u.hostname === STUB_HOST, { timeout: 20_000 });
    return new URL(page.url());
  }

  async function allow(page: Page): Promise<URL> {
    await page.locator("#allow").click();
    await page.waitForURL((u) => u.pathname === "/connect" && u.searchParams.has("status"), { timeout: 30_000 });
    return new URL(page.url());
  }

  test("the seller connects a shop and sees it on the Shops page; a used link is refused", async ({ browser }) => {
    const { context, page } = await signedIn(browser, "a@example.com");
    const authorize = await startConnecting(page);
    const cookies = await context.cookies();
    const session = cookies.find((c) => c.name === "session")!;
    const binding = cookies.find((c) => c.name === "listyro_oauth")!;
    // The production situation: the session cookie stays on the app's host, the binding cookie
    // reaches the API's host too.
    expect(session.domain).toBe("listyro.test");
    expect(binding.domain).toBe(".listyro.test");
    expect(binding.path).toBe("/api/auth/etsy");
    expect(binding.httpOnly).toBe(true);
    expect(binding.sameSite).toBe("Lax");
    const callback = await page.locator("#allow").getAttribute("href");
    expect(new URL(callback!).hostname).toBe(API_HOST);
    const back = await allow(page);
    expect(back.origin).toBe(APP);
    expect(back.searchParams.get("status")).toBe("connected");
    await expect(page.getByTestId("connect-banner")).toContainText("Shop connected");
    await expect(page.getByText("Fetching the shop's name…")).toBeVisible();
    expect((await context.cookies()).some((c) => c.name === "listyro_oauth")).toBe(false); // cleared
    // The same approval again (a reused state): refused, not a second connection.
    await page.goto(callback!);
    await page.waitForURL((u) => u.pathname === "/connect", { timeout: 20_000 });
    expect(new URL(page.url()).searchParams.get("status")).toBe("reused");
    await expect(page.getByTestId("connect-retry")).toBeVisible();
    expect(authorize.searchParams.get("state")).toBeTruthy();
    await context.close();
  });

  test("a consent link sent to another account's browser cannot attach that seller's shop", async ({ browser }) => {
    const a = await signedIn(browser, "a@example.com");
    const consent = await startConnecting(a.page); // A sends this link to B
    const b = await signedIn(browser, "b@example.com");
    await b.page.goto(consent.toString());
    const back = await allow(b.page);
    expect(back.searchParams.get("status")).toBe("no_binding");
    await expect(b.page.getByTestId("connect-banner")).toContainText(TOO_LONG);
    await expect(b.page.getByTestId("connect-retry")).toBeVisible();
    await expect(b.page.getByText("No shop connected yet.")).toBeVisible(); // B got no shop
    await a.context.close();
    await b.context.close();
  });

  test("an approval that took too long is refused with the clear message", async ({ browser }) => {
    const { context, page } = await signedIn(browser, "a@example.com");
    const authorize = await startConnecting(page);
    const state = authorize.searchParams.get("state")!;
    // Fifteen minutes later: the state is gone.
    execFileSync("docker", [...COMPOSE, "exec", "-T", "redis", "redis-cli", "DEL", `oauth:state:${state}`], { encoding: "utf8" });
    const back = await allow(page);
    expect(back.searchParams.get("status")).toBe("expired");
    await expect(page.getByTestId("connect-banner")).toContainText(TOO_LONG);
    await expect(page.getByTestId("connect-retry")).toBeVisible();
    await expect(page.getByText("not signed in")).toHaveCount(0);
    await context.close();
  });
});
