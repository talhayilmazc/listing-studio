// The search-readiness checklist on the review card.
//
// The server works it out with the app's own rules (backend pipeline/checklist.py;
// backend/tests/test_checklist.py tests each check). Here: the card shows it, asks
// the server again as the seller edits instead of judging any rule itself, never
// lets it block approval or a draft, and its words promise nothing.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";

const FRONTEND = path.join(import.meta.dirname, "..");
const read = (relative: string) => fs.readFileSync(path.join(FRONTEND, relative), "utf8").replace(/\r\n/g, "\n");
const code = (source: string) => source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[\s;{}(),])\/\/[^\n]*/g, "$1");
const COMPONENT = "components/SearchChecklist.tsx";
const CARD = "components/ReviewCard.tsx";

test("the review card shows the checklist and keeps it current as the seller edits", () => {
  const card = code(read(CARD));
  assert.match(card, /import \{ SearchChecklist \} from "\.\/SearchChecklist";/);
  assert.match(card, /<SearchChecklist checklist=\{checklist\} updating=\{checking\} \/>/);
  // It starts from the card's data, and edits are checked again by the server.
  assert.match(card, /useState<Checklist \| null>\(initial\.checklist \?\? null\)/);
  assert.match(card, /api\.previewChecklist\(initial\.id, \{ title, tags, description \}\)/);
  assert.match(card, /setTimeout\(recheck, \d+\)/);
  assert.match(card, /\}, \[title, tags, description\]\);/);
  // Saving brings the saved text's checklist; an Occasion, Holiday or Section change asks again.
  assert.match(card, /setChecklist\(res\.content\.checklist\)/);
  assert.match(card, /<ItemOptions [^>]*onChanged=\{recheck\}/);
  const api = code(read("lib/api.ts"));
  assert.match(api, /previewChecklist: \(id: string, [^)]*\) =>\n\s*req<Checklist>\(`\/content\/\$\{id\}\/checklist`, \{ method: "POST"/);
});

test("the checklist never blocks approval or a draft", () => {
  const card = code(read(CARD));
  for (const [, condition] of card.matchAll(/disabled=\{([^}]*)\}/g)) assert.doesNotMatch(condition, /checklist|check\b|checks/i, condition);
  assert.match(card, /checked=\{approved\}\n\s*disabled=\{!valid\}/, "approval still follows the existing validation only");
  assert.doesNotMatch(card, /checklist[^;\n]*(approve|createDraft)/i);
});

test("the component judges no rule: every check, the heading, the note and the summary come from the server", () => {
  const component = code(read(COMPONENT));
  for (const piece of ["{checklist.heading}", "{checklist.summary}", "{checklist.note}", "{check.label}", "{check.reason}"]) {
    assert.ok(component.includes(piece), piece);
  }
  // No limit, rule word or count of its own: those live in pipeline/checklist.py.
  // (The chevron icon's coordinates are drawing, not rules.)
  assert.doesNotMatch(component.replace(/<svg[\s\S]*?<\/svg>/g, ""), /\b(13|20|15|140|110)\b/);
  assert.doesNotMatch(component, /\.length\s*[<>]=?|split\(|toLowerCase\(|RegExp|validate/);
  assert.doesNotMatch(component, /Search-readiness checklist|checks pass|published guidance/, "the server's words are not typed again");
  // It opens by a tap (44px), not on hover, and the summary keeps its place while it updates.
  assert.match(component, /<button\n\s*type="button"\n\s*className="flex min-h-\[2\.75rem\]/);
  assert.match(component, /aria-expanded=\{open\}/);
  assert.match(component, /updating \? "opacity-50" : "opacity-100"/);
  assert.doesNotMatch(component, /onMouseEnter|hover:block|group-hover/);
});

test("its words promise nothing: no score, rank, guarantee or boost", () => {
  const forbidden = /\branking\b|\brank higher\b|\bguarantee|\bboost|etsy score|seo score/i;
  for (const [, quoted, shown] of read(COMPONENT).matchAll(/"([^"\n]*)"|>([^<{}\n]+)</g)) {
    assert.doesNotMatch(quoted ?? shown, forbidden, quoted ?? shown);
  }
  const backend = fs.readFileSync(path.join(FRONTEND, "..", "backend", "app", "pipeline", "checklist.py"), "utf8");
  for (const [, text] of backend.matchAll(/"([^"\n]*)"/g)) assert.doesNotMatch(text, forbidden, text);
  assert.match(backend, /^HEADING = "Search-readiness checklist"$/m);
  assert.match(backend, /^NOTE = "A check against Etsy's published guidance\. It does not predict how Etsy ranks your listing\."$/m);
});
