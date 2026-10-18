// Half-made drafts (components/UnfinishedDrafts.tsx, backend api/unfinished.py): one list in
// three parts (finish these, fix these, delete these on Etsy), and the words.
import type { UnfinishedDraft } from "./types";

/** "2 drafts were started on Etsy but not finished" */
export const unfinishedHeading = (n: number) =>
  `${n} ${n === 1 ? "draft was" : "drafts were"} started on Etsy but not finished`;

/** "1 draft needs a fix before it can be finished" */
export const fixHeading = (n: number) =>
  `${n} ${n === 1 ? "draft needs" : "drafts need"} a fix before ${n === 1 ? "it" : "they"} can be finished`;

/** "3 drafts to delete on Etsy" */
export const deleteHeading = (n: number) => `${n} ${n === 1 ? "draft" : "drafts"} to delete on Etsy`;

/** What "Finish these drafts" did. */
export function finishLine(started: number, skipped: number): string {
  const parts: string[] = [];
  if (started) parts.push(`Finishing ${started} ${started === 1 ? "draft" : "drafts"}: each carries on with the listing Etsy already has.`);
  if (skipped) parts.push(`${skipped} could not be started yet: the reason is under ${skipped === 1 ? "it" : "each"}.`);
  return parts.join(" ") || "Nothing to finish right now.";
}

/** Under the delete list: what these drafts are and who deletes them. */
export const DELETE_NOTE =
  "Each is a second draft of a design that has another draft in the same shop, or a draft whose batch was deleted (the app has no text left to finish it with). They stay on Etsy until you delete them in Shop Manager: the app never deletes anything on Etsy.";

/** Why a draft is in the delete list. */
export function deleteReason(item: Pick<UnfinishedDraft, "category" | "replaced_by" | "replaced_key">): string {
  if (item.category === "replaced" && item.replaced_by) {
    return `Replaced by listing ${item.replaced_by}${item.replaced_key ? ` (matched by ${item.replaced_key})` : ""}`;
  }
  return "Its batch was deleted: finish it in Shop Manager, or delete it there";
}

/** Why a draft is in the fix list, and what to do. */
export function fixReason(item: Pick<UnfinishedDraft, "category" | "personalization_problems" | "blocked">): string {
  if (item.category === "fix_personalization") {
    return `Etsy would refuse its personalization question: ${item.personalization_problems.join("; ")}. Change it on the listing's card, then finish it.`;
  }
  return item.blocked ? `${item.blocked}.` : "Its shop is not set up for this listing's profile yet.";
}

/** The three parts of the list, by the server's category (never judged here). */
export function sections(items: UnfinishedDraft[]) {
  return {
    finish: items.filter((i) => i.section === "finish"),
    fix: items.filter((i) => i.section === "fix"),
    remove: items.filter((i) => i.section === "delete"),
  };
}

/** The listing ids, one per line, to paste or check off in Shop Manager. */
export const replacedIds = (items: { shop_name: string; etsy_listing_id: number }[]) =>
  items.map((i) => `${i.shop_name}: ${i.etsy_listing_id}`).join("\n");
