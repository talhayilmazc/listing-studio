import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { join, resolve } from "node:path";

/**
 * Starts the shop-connection stack before the browser tests and stops it after them
 * (docker-compose.oauth-e2e.yml, e2e/oauth.spec.ts). It runs every time the layout and states
 * run does; OAUTH_E2E=off skips it, and the report then lists the three tests as skipped, with why.
 * The stack has no volumes: stopping it leaves nothing behind.
 */
// Playwright runs from frontend/ (npm run test:layout): the repository is its parent.
const ROOT = [resolve(process.cwd(), ".."), process.cwd()].find((d) => existsSync(join(d, "docker-compose.oauth-e2e.yml")))
  ?? resolve(process.cwd(), "..");
export const COMPOSE = ["compose", "-p", "listyro-oauth-e2e", "-f", join(ROOT, "docker-compose.oauth-e2e.yml")];

function docker(args: string[], timeout = 600_000): string {
  return execFileSync("docker", args, { cwd: ROOT, encoding: "utf8", timeout, stdio: ["ignore", "pipe", "pipe"] });
}

export default async function globalSetup(): Promise<(() => void) | void> {
  if (process.env.OAUTH_E2E === "off") return;
  try {
    docker(["image", "inspect", "listyro/frontend:latest"], 30_000);
  } catch {
    throw new Error("The shop-connection test needs the production frontend image: docker compose -f docker-compose.prod.yml build frontend");
  }
  docker([...COMPOSE, "up", "-d", "--build", "--wait"], 900_000);
  return () => {
    docker([...COMPOSE, "down"], 120_000);
  };
}
