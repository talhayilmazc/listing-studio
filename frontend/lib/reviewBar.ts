/**
 * The review page's action bar (fix/onboarding-followups, Part H): one sentence in plain words and
 * one primary action at any moment. The listings come first; "Where and when" opens only on "Send".
 *
 *   "2 approved listings will go to 4 shops as drafts. 2 more are waiting for your approval."
 *
 * The numbers are the server's (the send preview: listings, shops, when); nothing is counted here
 * that the panel would count differently.
 */
import type { SendPreview, SendWhen } from "./types";

const HOW: Record<SendWhen, string> = {
  drafts: "as drafts",
  publish: "and go live now",
  spread: "on the schedule you set",
};

const n = (k: number, one: string, many: string) => `${k} ${k === 1 ? one : many}`;

export function summaryLine(preview: Pick<SendPreview, "listings" | "shops" | "when"> | null, approved: number, waiting: number): string {
  const parts: string[] = [];
  if (preview && preview.listings > 0) {
    parts.push(`${n(preview.listings, "approved listing", "approved listings")} will go to ${n(preview.shops, "shop", "shops")} ${HOW[preview.when]}.`);
  } else if (approved > 0 && preview) {
    parts.push("Every approved listing is already in its shops.");
  } else if (waiting > 0) {
    parts.push("Approve listings to send them.");
  }
  if (waiting > 0) {
    parts.push(approved > 0 || (preview?.listings ?? 0) > 0
      ? `${waiting} more ${waiting === 1 ? "is" : "are"} waiting for your approval.`
      : `${n(waiting, "listing is", "listings are")} waiting for your approval.`);
  }
  return parts.join(" ");
}

export type Primary =
  | { kind: "approve-selected"; count: number; label: string }
  | { kind: "approve-this"; label: string }
  | { kind: "send"; count: number; label: string }
  | { kind: "next"; label: string }
  | null;

/**
 * The page's one primary action, and its label says exactly what it does (hotfix/review-regressions:
 * "Approve" pressed a button on a card the seller could not see, and said nothing when that did nothing):
 * approve what is selected ("Approve 3 selected"); else the listing on screen ("Approve this listing");
 * else send what is approved; else show the next listing that waits.
 */
export function primaryAction(o: {
  selectedUnapproved: number; currentUnapproved: boolean; toSend: number; unapproved: number; example: boolean;
}): Primary {
  if (o.selectedUnapproved > 0) return { kind: "approve-selected", count: o.selectedUnapproved, label: `Approve ${o.selectedUnapproved} selected` };
  if (o.currentUnapproved) return { kind: "approve-this", label: "Approve this listing" };
  if (o.toSend > 0 && !o.example) return { kind: "send", count: o.toSend, label: `Send (${o.toSend})` };
  if (o.unapproved > 0) return { kind: "next", label: "Show the next one to approve" };
  return null;
}

/** "Send (n)" beside an approve action: approved listings can go while others still wait. */
export function sendBeside(primary: Primary, toSend: number, example: boolean): string | null {
  if (!primary || primary.kind === "send" || example || toSend <= 0) return null;
  return `Send (${toSend})`;
}

/** Under More, with a confirmation that states the count. */
export const approveAllLabel = (waiting: number): string => `Approve all ${waiting} waiting`;
export const approveAllQuestion = (waiting: number): string =>
  `Approve all ${waiting} ${waiting === 1 ? "listing that is" : "listings that are"} waiting? One that can't be approved is left as it is and says why on its card.`;

/** After an approval: what happened, said in the bar at once ("Approved. 14 waiting."). */
export function approvedLine(approved: number, blocked: number, waiting: number): string {
  const parts: string[] = [];
  if (approved > 0) parts.push(approved === 1 ? "Approved." : `Approved ${approved}.`);
  if (blocked > 0) parts.push(blocked === 1 ? "1 listing can't be approved yet: its card says why." : `${blocked} listings can't be approved yet: their cards say why.`);
  if (approved > 0 || blocked > 0) parts.push(waiting > 0 ? `${waiting} waiting.` : "None waiting.");
  return parts.join(" ");
}

/** The one action that fixes what blocks an approval: the field its reason names. */
export function fixFor(reason: string): { field: "title" | "tags"; label: string } | null {
  const text = reason.toLowerCase();
  if (/\(title\b|\btitle\b/.test(text)) return { field: "title", label: "Fix the title" };
  if (/\(tag\b|\btags?\b/.test(text)) return { field: "tags", label: "Fix the tags" };
  return null;
}

/** The account's trademark filter stopped it ("Remove: Mickey (title)"): the seller may keep the name. */
export const stoppedByBrand = (reason: string): boolean => /(^|; )Remove: /.test(reason);

/**
 * Which listing is "this listing": the card under a line 40% down the screen, else the nearest to it.
 * ``cards``: each card's top and bottom on screen, in order.
 */
export function onScreen(cards: { top: number; bottom: number }[], height: number): number {
  if (cards.length === 0) return -1;
  const line = height * 0.4;
  let best = 0;
  let nearest = Infinity;
  for (const [i, c] of cards.entries()) {
    if (c.top <= line && c.bottom >= line) return i;
    const distance = Math.min(Math.abs(c.top - line), Math.abs(c.bottom - line));
    if (distance < nearest) {
      nearest = distance;
      best = i;
    }
  }
  return best;
}
