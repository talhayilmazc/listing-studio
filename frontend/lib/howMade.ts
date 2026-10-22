/**
 * How it's made: Etsy's Creativity Standards questions, answered once per profile
 * (backend pipeline/how_made.py; the same keys and words). Etsy's API has no field for
 * either, so nothing is sent; the choice is kept and every draft sends it the day Etsy adds
 * the field. The seller does nothing per listing for it, and nothing about it holds up a
 * draft, a schedule or a publish (docs/etsy-how-its-made.md).
 */

/** The one line under the selector (the owner's wording, 2026-10-23). */
export const NOT_SENT =
  "Etsy doesn't let apps set this yet, so it can't be sent automatically. You can set it for many listings at once in Shop Manager → Listings → select → Editing options.";

export const PRODUCTION: { key: string; label: string; short: string }[] = [
  { key: "made_from_scratch", label: "Made from scratch", short: "made from scratch" },
  { key: "assembled", label: "Assembled from purchased parts", short: "assembled from purchased parts" },
  { key: "altered", label: "An item my shop alters", short: "shop alters it" },
  { key: "curated", label: "A curated set of purchased goods", short: "curated set of purchased goods" },
  { key: "natural", label: "A natural material", short: "natural material" },
];

export const TOOLS: { key: string; label: string; short: string }[] = [
  { key: "handheld", label: "Handheld or hand-guided tools", short: "handheld tools" },
  { key: "computerized", label: "Computerized tools or machines", short: "computerized tools" },
  { key: "ai", label: "An AI generator", short: "AI generator" },
  { key: "none", label: "None", short: "no tools" },
];

export const NONE = "none";

/** Ticking a tool: "none" stands alone, so ticking it clears the others and ticking another clears it. */
export function toggleTool(chosen: string[], key: string, on: boolean): string[] {
  if (!on) return chosen.filter((t) => t !== key);
  if (key === NONE) return [NONE];
  return TOOLS.map((t) => t.key).filter((t) => t !== NONE && (t === key || chosen.includes(t)));
}

/** Why the answers cannot be saved yet, or null (the server checks the same: pipeline/how_made.clean). */
export function problem(production: string | null, tools: string[]): string | null {
  if (!production || !PRODUCTION.some((p) => p.key === production)) return "Choose how your shop produces this item.";
  if (tools.length === 0) return "Choose the tools used, or None.";
  if (tools.includes(NONE) && tools.length > 1) return "\"None\" cannot be chosen with other tools.";
  if (tools.some((t) => !TOOLS.some((x) => x.key === t))) return "Unknown tool.";
  return null;
}

/** "shop alters it · computerized tools", or null when not set. */
export function shortWords(production: string | null | undefined, tools: string[] | null | undefined): string | null {
  const p = PRODUCTION.find((x) => x.key === production);
  if (!p || !tools || tools.length === 0) return null;
  const words = TOOLS.filter((t) => tools.includes(t.key)).map((t) => t.short);
  return `${p.short} · ${words.join(", ")}`;
}

/** The profile card's line. */
export function cardLine(production: string | null | undefined, tools: string[] | null | undefined): string {
  const words = shortWords(production, tools);
  return words ? `How it's made: ${words}` : "How it's made: not set";
}
