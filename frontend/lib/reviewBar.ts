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
  | { kind: "send"; count: number; label: string }
  | { kind: "approve"; label: string }
  | null;

/** The page's one primary action: approve what is selected, else send what is approved, else approve the next one. */
export function primaryAction(o: { selectedUnapproved: number; toSend: number; unapproved: number; example: boolean }): Primary {
  if (o.selectedUnapproved > 0) return { kind: "approve-selected", count: o.selectedUnapproved, label: `Approve selected (${o.selectedUnapproved})` };
  if (o.toSend > 0 && !o.example) return { kind: "send", count: o.toSend, label: `Send (${o.toSend})` };
  if (o.unapproved > 0) return { kind: "approve", label: "Approve" };
  return null;
}
