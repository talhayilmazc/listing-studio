/**
 * What Etsy itself refuses in a draft's title and tags: the copy of
 * backend/app/pipeline/etsy_text.py, for instant feedback on a listing the seller
 * writes. Both read backend/tests/fixtures/etsy_text_cases.json, so they agree.
 *
 * From Etsy's OpenAPI spec for createDraftListing: a title holds only letters,
 * numbers, punctuation, mathematical symbols, spaces, ™, © and ®, and %, :, & and
 * + at most once each; a tag holds only letters, numbers, spaces, -, ', ™, © and
 * ®. A title of 140 characters at most and 13 tags of 20 at most are Etsy's
 * documented limits. The app's own quality rules (exactly 13 tags, the product
 * type named...) are the server's warnings on such a listing, never blockers.
 */
export const MAX_TITLE = 140;
export const MAX_TAGS = 13;
export const MAX_TAG = 20;
const ONCE = ["%", ":", "&", "+"];
const TITLE_OK = /^[\p{L}\p{Nd}\p{P}\p{Sm}\p{Zs}™©®]$/u;
const TAG_OK = /^[\p{L}\p{Nd}\p{Zs}\-'™©®]$/u;

function shown(chars: string[]): string {
  return [...new Set(chars)].map((ch) => (/\s/.test(ch) ? `U+${ch.codePointAt(0)!.toString(16).toUpperCase().padStart(4, "0")}` : `'${ch}'`)).join(" ");
}

/** Why Etsy would refuse this title (none: it would not). */
export function titleErrors(title: string): string[] {
  if (!title.trim()) return ["Title is empty"];
  const errors: string[] = [];
  const chars = [...title];
  if (chars.length > MAX_TITLE) errors.push(`Title is over Etsy's ${MAX_TITLE} characters (${chars.length})`);
  const bad = chars.filter((ch) => !TITLE_OK.test(ch));
  if (bad.length) errors.push(`Title has characters Etsy does not allow: ${shown(bad)}`);
  const twice = ONCE.filter((ch) => chars.filter((c) => c === ch).length > 1);
  if (twice.length) errors.push(`Title uses ${twice.join(" ")} more than once; Etsy allows %, :, & and + once each`);
  return errors;
}

/** Why Etsy would refuse these tags (none: it would not). */
export function tagErrors(tags: string[]): string[] {
  const errors: string[] = [];
  if (tags.length > MAX_TAGS) errors.push(`More than ${MAX_TAGS} tags (${tags.length}); Etsy takes ${MAX_TAGS} at most`);
  for (const tag of tags) {
    if (!tag.trim()) {
      errors.push("A tag is empty");
      continue;
    }
    if ([...tag].length > MAX_TAG) errors.push(`Tag over ${MAX_TAG} characters: "${tag}"`);
    if (tag.includes(",")) errors.push(`Tag has a comma: "${tag}"`);
    const bad = [...tag].filter((ch) => ch !== "," && !TAG_OK.test(ch));
    if (bad.length) errors.push(`Tag has characters Etsy does not allow: "${tag}" (${shown(bad)})`);
  }
  return [...new Set(errors)];
}

/** Everything in a listing's own text that Etsy would refuse for a draft. */
export function draftErrors(title: string, tags: string[], description: string): string[] {
  const errors = [...titleErrors(title), ...tagErrors(tags)];
  if (!description.trim()) errors.push("Description is empty");
  return errors;
}
