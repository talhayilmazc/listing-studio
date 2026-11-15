// Browser tests write their pictures into test-results/ (git-ignored), never into docs/screens/: a run must not leave
// untracked files in the repository. A picture meant for the docs is committed by hand, or behind its own switch
// (BRAND_SHOTS=1).
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { test } from "node:test";

test("no browser test writes into docs/screens unless a switch asks for it", () => {
  const folder = new URL("../e2e/", import.meta.url);
  for (const name of readdirSync(folder).filter((n) => n.endsWith(".ts"))) {
    const lines = readFileSync(new URL(name, folder), "utf8").split("\n");
    lines.forEach((line, i) => {
      if (!/screenshot\(\{[^}]*path:\s*[`"'][^`"']*docs\/screens/.test(line)) return;
      const before = lines.slice(Math.max(0, i - 6), i).join("\n");
      assert.ok(/process\.env\.[A-Z_]+/.test(before), `${name}:${i + 1} writes into docs/screens on every run; use test-results/`);
    });
  }
});
