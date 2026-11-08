/**
 * The review card's live checks (components/LiveChecks.tsx), as the seller types: the title's
 * length against Etsy's 140, the 13-tag limit, tags over 20 characters, the same tag twice (or the
 * same root words written two ways) and the trademark warning. Each comes from the rule the free
 * title and tag checkers use (lib/etsyText.ts, lib/listingRules.ts, lib/brandSpot.ts), never a
 * second copy, so the app and the tools always say the same thing.
 */
import { brandWarnings } from "./brandSpot.ts";
import { MAX_TAG, MAX_TAGS, MAX_TITLE, tagParts } from "./etsyText.ts";
import { sameSearchTags } from "./listingRules.ts";

export type Check = { key: string; ok: boolean; line: string };

export function liveChecks(title: string, tags: string[]): { checks: Check[]; brands: string[] } {
  const length = [...title].length;
  const long = tagParts(tags).long;
  const seen = new Set<string>();
  const twice = tags.filter((t) => {
    const k = t.trim().toLowerCase();
    if (seen.has(k)) return true;
    seen.add(k);
    return false;
  });
  const similar = sameSearchTags(tags);
  const checks: Check[] = [
    { key: "title", ok: length > 0 && length <= MAX_TITLE, line: length > MAX_TITLE ? `Title: ${length} of ${MAX_TITLE} characters, ${length - MAX_TITLE} over` : length === 0 ? "Title: empty" : `Title: ${length} of ${MAX_TITLE} characters` },
    { key: "count", ok: tags.length > 0 && tags.length <= MAX_TAGS, line: tags.length > MAX_TAGS ? `Tags: ${tags.length} of ${MAX_TAGS}, ${tags.length - MAX_TAGS} too many` : `Tags: ${tags.length} of ${MAX_TAGS}` },
    { key: "length", ok: long.length === 0, line: long.length ? `Over ${MAX_TAG} characters: ${long.join(", ")}` : `Every tag ${MAX_TAG} characters or fewer` },
    {
      key: "twice",
      ok: twice.length === 0 && similar.length === 0,
      line: twice.length ? `Same tag twice: ${[...new Set(twice)].join(", ")}`
        : similar.length ? `Same words twice: ${similar.map(([a, b]) => `${a} / ${b}`).join(", ")}`
        : "No tag repeats another",
    },
  ];
  return { checks, brands: brandWarnings(title, tags) };
}
