// Tag entry (v7 §E4): "nurse, winter, sweatshirt", typed or pasted, is three
// tags. Every tag is checked against Etsy's rules as it is entered.

export const MAX_TAG_LENGTH = 20;
export const TAG_COUNT = 13;

/** The tags in a piece of text: split on commas, semicolons and new lines. */
export function splitTags(text: string): string[] {
  return text
    .split(/[,;\n\r]+/)
    .map((t) => t.replace(/\s+/g, " ").trim())
    .filter(Boolean);
}

/** ``existing`` plus the new tags in ``text``, skipping ones already there (any case). */
export function addTags(existing: string[], text: string): string[] {
  const out = [...existing];
  const seen = new Set(existing.map((t) => t.toLowerCase()));
  for (const t of splitTags(text)) {
    if (!seen.has(t.toLowerCase())) {
      out.push(t);
      seen.add(t.toLowerCase());
    }
  }
  return out;
}

/** Replace tag ``i`` with what was typed; a comma in it makes it several tags. */
export function editTag(tags: string[], i: number, value: string): string[] {
  if (!/[,;\n]/.test(value)) return tags.map((t, idx) => (idx === i ? value : t));
  const others = tags.filter((_, idx) => idx !== i);
  const pieces = splitTags(value).filter((p) => !others.some((o) => o.toLowerCase() === p.toLowerCase()));
  return [...tags.slice(0, i), ...pieces, ...tags.slice(i + 1)];
}

/** What is wrong with each tag, if anything (the same rules the server applies). */
export function tagProblems(tags: string[]): (string | null)[] {
  const counts = new Map<string, number>();
  for (const t of tags) counts.set(t.trim().toLowerCase(), (counts.get(t.trim().toLowerCase()) ?? 0) + 1);
  return tags.map((t) => {
    if (!t.trim()) return "empty";
    if (t.length > MAX_TAG_LENGTH) return `over ${MAX_TAG_LENGTH} characters`;
    if ((counts.get(t.trim().toLowerCase()) ?? 0) > 1) return "duplicate";
    return null;
  });
}
