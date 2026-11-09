// No page, legal text or message names a person or a personal mailbox (fix/onboarding-followups, Part G).
// The support address comes from the server's SUPPORT_EMAIL (default support@listyro.com) through
// /api/meta; a personal mailbox there is replaced on the server. The one exception is the provider
// line Etsy's API terms ask for, word for word (PROVIDER_DISCLAIMER in lib/legal.ts). Rendered pages
// are checked the same way in e2e/layout.spec.ts (expectSound).
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { PROVIDER_DISCLAIMER } from "./legal.ts";

const root = fileURLToPath(new URL("..", import.meta.url));
const PERSONAL = /gmail\.com|talha/i;

function files(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(join(root, dir))) {
    const rel = join(dir, name);
    if (statSync(join(root, rel)).isDirectory()) out.push(...files(rel));
    else if (/\.(tsx?|mjs|md|json)$/.test(name) && !/\.test\.ts$/.test(name)) out.push(rel);
  }
  return out;
}

test("no source of a page names a person or a personal mailbox, except the provider line", () => {
  const found: string[] = [];
  for (const file of [...files("app"), ...files("components"), ...files("lib"), ...files("public")].filter((f) => !f.endsWith(".png"))) {
    readFileSync(join(root, file), "utf8").split("\n").forEach((line, i) => {
      if (PERSONAL.test(line) && !line.includes(PROVIDER_DISCLAIMER) && !line.includes(PROVIDER_DISCLAIMER.slice(0, 40))) {
        found.push(`${file}:${i + 1}`);
      }
    });
  }
  assert.deepEqual(found, []);
});

test("the support address is read from the server, never written into a page", () => {
  for (const file of ["components/Footer.tsx", "components/AuthShell.tsx", "components/marketing/SupportAddress.tsx"]) {
    assert.ok(readFileSync(join(root, file), "utf8").includes("support_email"), file);
  }
});
