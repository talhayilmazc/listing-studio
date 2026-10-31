import { defineConfig } from "@playwright/test";

/**
 * Layout tests of Listyro's own pages (e2e/layout.spec.ts), run against a server
 * that is already up:
 *
 *   npm run test:layout                                  the public pages and the sign-in screens
 *   LAYOUT_EMAIL=… LAYOUT_PASSWORD=… npm run test:layout   also the signed-in pages and every Admin tab
 *
 * The signed-in half needs the synthetic demo admin (never a real account):
 *   docker compose exec -T api python -m app.cli seed-demo --admin      (prints the password)
 *   docker compose exec -T api python -m app.cli seed-demo --remove     (afterwards)
 *
 * LAYOUT_BASE_URL chooses the server (default: the development stack). A
 * production build is the better subject, because it is what is deployed. It must be on a port
 * the API trusts (CORS_ORIGINS, FRONTEND_URL): on any other, signing in is refused as cross-site
 * and every signed-in test fails at the sign-in screen:
 *   docker compose -f docker-compose.prod.yml build frontend
 *   docker run --rm --network etsy-assistant_default -p 3000:3000 listyro/frontend:latest
 *   LAYOUT_BASE_URL=http://localhost:3000 npm run test:layout
 *
 * Every run also connects a shop in a real browser (e2e/oauth.spec.ts): oauth.global.ts starts
 * its own stack (docker-compose.oauth-e2e.yml: the production frontend image, the API on a second
 * host as Etsy's redirect back, a stub "Etsy authorize" page on a third) and stops it afterwards.
 * Its hosts end in .test and the browser maps them to this machine (HOSTS below); the stub is our
 * own page, and the spec aborts any request to Etsy's site. OAUTH_E2E=off skips the stack.
 *
 * This browser is pointed at our own pages and nowhere else. Etsy's site is
 * reached only through its API, never through a browser (CLAUDE.md, rule 1):
 * the address is checked here, so the tests cannot be aimed at anything else.
 */
const BASE = process.env.LAYOUT_BASE_URL ?? "http://localhost:3000";
const OURS = new Set(["localhost", "127.0.0.1", "listyro.com", "www.listyro.com"]);
if (!OURS.has(new URL(BASE).hostname)) {
  throw new Error(`LAYOUT_BASE_URL must be one of our own hosts (${[...OURS].join(", ")}), not ${new URL(BASE).hostname}`);
}

/** The shop-connection test's three hosts, all this machine: app, API (Etsy's redirect back), stub. */
const HOSTS = "MAP listyro.test 127.0.0.1, MAP api.listyro.test 127.0.0.1, MAP etsy-stub.test 127.0.0.1";
const launchOptions = { args: [`--host-resolver-rules=${HOSTS}`] };

export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/oauth.global.ts",
  outputDir: "./test-results",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 90_000,
  reporter: [["list"]],
  use: { baseURL: BASE, trace: "off", video: "off", screenshot: "off" },
  projects: [
    { name: "phone-375", use: { browserName: "chromium", viewport: { width: 375, height: 800 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2, launchOptions } },
    { name: "tablet-768", use: { browserName: "chromium", viewport: { width: 768, height: 1000 }, hasTouch: true, launchOptions } },
    { name: "desktop-1366", use: { browserName: "chromium", viewport: { width: 1366, height: 900 }, launchOptions } },
  ],
});
