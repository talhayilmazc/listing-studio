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

const CURLY = new Set(["’", "‘", "ʼ"]);
const DASHES = new Set(["–", "—", "‒"]);

/** The plain fix for characters Etsy refuses (backend etsy_text._fix). */
function fix(bad: string[], tag: boolean): string {
  const chars = new Set(bad);
  const hints: string[] = [];
  const has = (set: Set<string>) => [...chars].some((c) => set.has(c));
  if (has(CURLY)) hints.push("use a straight apostrophe (') instead of a curly one");
  if (tag && has(DASHES)) hints.push("use a hyphen (-) instead of a dash");
  if (tag && chars.has("&")) hints.push('write "and" instead of &');
  const rest = [...chars].filter((c) => !CURLY.has(c) && !(tag && (DASHES.has(c) || c === "&")));
  if (rest.length) hints.push(hints.length ? "remove the others (emoji, symbols, tabs)" : "remove them (emoji, symbols, tabs)");
  return hints.join("; ");
}

/** What is wrong with a title, piece by piece (the free title checker shows each on its own line). */
export function titleParts(title: string): { length: number; bad: string[]; fix: string; twice: string[] } {
  const chars = [...title];
  const bad = chars.filter((ch) => !TITLE_OK.test(ch));
  return { length: chars.length, bad, fix: bad.length ? fix(bad, false) : "", twice: ONCE.filter((ch) => chars.filter((c) => c === ch).length > 1) };
}

/** Why Etsy would refuse this title (none: it would not). */
export function titleErrors(title: string): string[] {
  if (!title.trim()) return ["Title is empty"];
  const errors: string[] = [];
  const { length, bad, twice } = titleParts(title);
  if (length > MAX_TITLE) errors.push(`Title is over Etsy's ${MAX_TITLE} characters (${length})`);
  if (bad.length) errors.push(`Title has characters Etsy does not allow: ${shown(bad)}: ${fix(bad, false)}`);
  if (twice.length)
    errors.push(`Title uses ${twice.join(" ")} more than once; Etsy allows %, :, & and + once each${twice.includes("&") ? ' (write "and" for the other &)' : ""}`);
  return errors;
}

const badInTag = (tag: string): string[] => [...tag].filter((ch) => ch !== "," && !TAG_OK.test(ch));

/** What is wrong with a set of tags, piece by piece (the free tag checker shows each on its own line). */
export function tagParts(tags: string[]): { long: string[]; bad: { tag: string; chars: string[]; fix: string }[] } {
  return {
    long: tags.filter((tag) => [...tag].length > MAX_TAG),
    bad: tags.filter((tag) => badInTag(tag).length).map((tag) => ({ tag, chars: badInTag(tag), fix: fix(badInTag(tag), true) })),
  };
}

/** The tags that start with ' or -. A note, never a stop: backend etsy_text.tags_starting_badly. */
export const tagsStartingBadly = (tags: string[]): string[] => tags.filter((tag) => /^['-]/.test(tag.trim()));

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
    const bad = badInTag(tag);
    if (bad.length) errors.push(`Tag has characters Etsy does not allow: "${tag}" (${shown(bad)}): ${fix(bad, true)}`);
  }
  return [...new Set(errors)];
}

/** Everything in a listing's own text that Etsy would refuse for a draft. */
export function draftErrors(title: string, tags: string[], description: string): string[] {
  const errors = [...titleErrors(title), ...tagErrors(tags)];
  if (!description.trim()) errors.push("Description is empty");
  return errors;
}
