import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { expect, type Page } from "@playwright/test";

/**
 * One sign-in for the whole browser run, and every test reuses its session. The API allows 20
 * sign-ins per address in 15 minutes (core/ratelimit.py): with one per test the run hit that limit
 * and each test after it failed at the sign-in screen until the window ended.
 *
 * The session is kept in test-results/ (git-ignored, emptied at the start of each run), so it
 * outlives the fresh worker Playwright starts after a failed test.
 */
const SAVED = join(process.cwd(), "test-results", ".signed-in.json");

async function savedSessionWorks(page: Page): Promise<boolean> {
  if (!existsSync(SAVED)) return false;
  await page.context().addCookies(JSON.parse(readFileSync(SAVED, "utf8")));
  const answer = await page.request.get("/api/account/session");
  if (answer.status() === 204) return true;
  await page.context().clearCookies();
  return false;
}

export async function signIn(page: Page, email: string, password: string): Promise<void> {
  if (await savedSessionWorks(page)) {
    await page.goto("/dashboard");
  } else {
    await page.goto("/login");
    await page.locator("#email").fill(email);
    await page.locator("#password").fill(password);
    await page.locator('button[type="submit"]').click();
    await page.waitForURL(/\/(dashboard|accept-terms|password)/, { timeout: 30_000 });
    mkdirSync(dirname(SAVED), { recursive: true });
    writeFileSync(SAVED, JSON.stringify(await page.context().cookies()));
  }
  expect(new URL(page.url()).pathname, "the demo account signs straight in").toBe("/dashboard");
}
