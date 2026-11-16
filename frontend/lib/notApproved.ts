/**
 * A listing that could not be approved says why on its own card, with the one action that fixes it
 * (hotfix/review-regressions, kept when the review and send flow went back to the 13 November design).
 */

/** The one action that fixes what blocks an approval: the field its reason names. */
export function fixFor(reason: string): { field: "title" | "tags"; label: string } | null {
  const text = reason.toLowerCase();
  if (/\(title\b|\btitle\b/.test(text)) return { field: "title", label: "Fix the title" };
  if (/\(tag\b|\btags?\b/.test(text)) return { field: "tags", label: "Fix the tags" };
  return null;
}

/** The account's trademark filter stopped it ("Remove: Mickey (title)"): the seller may keep the name. */
export const stoppedByBrand = (reason: string): boolean => /(^|; )Remove: /.test(reason);
