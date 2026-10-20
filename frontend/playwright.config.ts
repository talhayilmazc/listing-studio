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
 * production build is the better subject, because it is what is deployed:
 *   docker compose -f docker-compose.prod.yml build frontend
 *   docker run --rm --network etsy-assistant_default -p 3100:3000 listyro/frontend:latest
 *   LAYOUT_BASE_URL=http://localhost:3100 npm run test:layout
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

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 90_000,
  reporter: [["list"]],
  use: { baseURL: BASE, trace: "off", video: "off", screenshot: "off" },
  projects: [
    { name: "phone-375", use: { browserName: "chromium", viewport: { width: 375, height: 800 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 } },
    { name: "tablet-768", use: { browserName: "chromium", viewport: { width: 768, height: 1000 }, hasTouch: true } },
    { name: "desktop-1366", use: { browserName: "chromium", viewport: { width: 1366, height: 900 } } },
  ],
});
