// The listing style's words (lib/listingStyle.ts) and every word the app and the site
// show: they describe what the "Short, clear title" style does, never what it
// achieves. Etsy has published no figures on views, clicks, sales or ranking for any
// title style, so a claim of that kind anywhere in the copy fails this test.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import {
  COMPARISON_LABELS,
  PERFORMANCE_CLAIMS,
  SEARCH_CARD_NOTE,
  SEARCH_RULES_NOTE,
  STYLE_HELP,
  STYLE_LABELS,
  TITLE_WARNING_HEAD,
} from "./listingStyle.ts";

const FRONTEND = path.join(import.meta.dirname, "..");
const read = (relative: string) => fs.readFileSync(path.join(FRONTEND, relative), "utf8").replace(/\r\n/g, "\n");

function sources(dir: string): string[] {
  const out: string[] = [];
  for (const entry of fs.readdirSync(path.join(FRONTEND, dir), { withFileTypes: true })) {
    const rel = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...sources(rel));
    else if (/\.(ts|tsx)$/.test(entry.name) && !entry.name.endsWith(".test.ts") && rel !== path.join("lib", "listingStyle.ts")) out.push(rel);
  }
  return out;
}

/** A sentence that asks, or says it does not: "Will my listings rank higher?", "an aid, not a guarantee". */
const DENIES = /\?|\b(no|not|never|nobody|cannot|can't|don't|doesn't|does not|do not|without|değil|etmez|yok)\b/i;

test("the short style says what it does, in the words agreed", () => {
  assert.equal(
    STYLE_HELP.search,
    "Follows Etsy's current title guidance: a short, clear title with the item and its key details first. Other keywords go to tags and the description.",
  );
  assert.equal(SEARCH_CARD_NOTE, STYLE_HELP.search);
  assert.match(SEARCH_RULES_NOTE, /Fewer than 15 words/);
  assert.match(SEARCH_RULES_NOTE, /never cut/);
  assert.match(TITLE_WARNING_HEAD, /not cut/);
});

test("no label reads as Etsy's endorsement, and every screen uses the same labels", () => {
  for (const label of Object.values(STYLE_LABELS)) assert.doesNotMatch(label, /etsy/i, label);
  assert.deepEqual(COMPARISON_LABELS, { short: STYLE_LABELS.search, long: STYLE_LABELS.classic });
  const definitions = read("lib/analyticsDefinitions.ts");
  assert.ok(definitions.includes(`("${STYLE_LABELS.search}" or "${STYLE_LABELS.classic}")`));
  for (const file of ["components/ListingStyle.tsx", "components/ReviewCard.tsx", "components/analytics/TitleStyles.tsx"]) {
    const text = read(file);
    assert.doesNotMatch(text, /Etsy recommended/, file);
    assert.doesNotMatch(text, /optimi[sz]ed for search/i, file);
  }
  assert.match(read("components/ListingStyle.tsx"), /STYLE_HELP\[value\]/);
  assert.match(read("components/ReviewCard.tsx"), /data-testid="title-warnings"/);
});

test("no performance claim anywhere in the app's or the site's copy", () => {
  const found: string[] = [];
  for (const file of [...sources("app"), ...sources("components"), ...sources("lib")]) {
    const text = read(file);
    for (const claim of PERFORMANCE_CLAIMS) {
      for (const m of text.matchAll(new RegExp(claim.source, "gi"))) {
        // The sentence around it: a question or a denial is not a claim.
        const start = Math.max(text.lastIndexOf(".", m.index!), text.lastIndexOf('"', m.index!), text.lastIndexOf("\n", m.index!));
        const end = text.indexOf(".", m.index!);
        const sentence = text.slice(start + 1, end === -1 ? undefined : end + 1);
        if (!DENIES.test(sentence)) found.push(`${file}: ${m[0]} — ${sentence.trim().slice(0, 120)}`);
      }
    }
  }
  assert.deepEqual(found, []);
});

test("the claim patterns catch the claims they are for", () => {
  const caught = (s: string) => PERFORMANCE_CLAIMS.some((p) => p.test(s));
  for (const s of ["Get more views", "Boost your sales", "Rank higher on Etsy", "The best for 2026", "Get found more", "Guaranteed results",
    "Daha fazla satış", "Aramada üst sıraya", "2026 için en iyi"]) {
    assert.ok(caught(s), s);
  }
  assert.ok(!caught(STYLE_HELP.search) && !caught(SEARCH_RULES_NOTE));
});
