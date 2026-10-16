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
