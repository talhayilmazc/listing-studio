// Half-made drafts (components/UnfinishedDrafts.tsx, backend api/unfinished.py): the words.

/** "2 drafts were started on Etsy but not finished" */
export const unfinishedHeading = (n: number) =>
  `${n} ${n === 1 ? "draft was" : "drafts were"} started on Etsy but not finished`;

/** What "Finish these drafts" did. */
export function finishLine(started: number, skipped: number): string {
  const parts: string[] = [];
  if (started) parts.push(`Finishing ${started} ${started === 1 ? "draft" : "drafts"}: each carries on with the listing Etsy already has.`);
  if (skipped) parts.push(`${skipped} still ${skipped === 1 ? "needs" : "need"} its shop set up first.`);
  return parts.join(" ") || "Nothing to finish right now.";
}

/** "3 drafts were replaced by newer drafts" */
export const replacedHeading = (n: number) =>
  `${n} ${n === 1 ? "draft was" : "drafts were"} replaced by a newer draft`;

/** Under the replaced list: what they are and who deletes them. */
export const REPLACED_NOTE =
  "A newer draft of the same design finished in the same shop. These older drafts stay on Etsy until you delete them in Shop Manager: the app never deletes anything on Etsy.";

/** The listing ids, one per line, to paste or check off in Shop Manager. */
export const replacedIds = (items: { shop_name: string; etsy_listing_id: number }[]) =>
  items.map((i) => `${i.shop_name}: ${i.etsy_listing_id}`).join("\n");
