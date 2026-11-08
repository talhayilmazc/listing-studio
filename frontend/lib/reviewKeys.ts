/**
 * The review screen's keys (app/(app)/batches/[id]/review/page.tsx): J and K the next and previous
 * listing, A approve it, E its title, T its tags, ? the list of keys. They never fire while the
 * seller types in a field, or with Ctrl, Cmd or Alt held (the browser's own shortcuts stay theirs).
 */
export type KeyAction = "next" | "previous" | "approve" | "title" | "tags" | "help" | "close";

export const SHORTCUTS: { keys: string; does: string }[] = [
  { keys: "J", does: "Next listing" },
  { keys: "K", does: "Previous listing" },
  { keys: "A", does: "Approve, or take the approval back" },
  { keys: "E", does: "Edit the title" },
  { keys: "T", does: "Go to the tags" },
  { keys: "?", does: "Show these keys" },
  { keys: "Esc", does: "Close this list, or leave a field" },
];

/** What a key press does, or null: a typing field, a modifier, or a key that means nothing here. */
export function actionFor(e: { key: string; ctrlKey?: boolean; metaKey?: boolean; altKey?: boolean; target?: unknown }): KeyAction | null {
  if (e.ctrlKey || e.metaKey || e.altKey) return null;
  const t = e.target as { tagName?: string; isContentEditable?: boolean; type?: string } | null | undefined;
  const tag = (t?.tagName ?? "").toUpperCase();
  const typing = tag === "TEXTAREA" || tag === "SELECT" || Boolean(t?.isContentEditable)
    || (tag === "INPUT" && !["checkbox", "radio", "button", "submit"].includes((t?.type ?? "text").toLowerCase()));
  if (e.key === "Escape") return "close";
  if (typing) return null;
  switch (e.key) {
    case "j": case "J": return "next";
    case "k": case "K": return "previous";
    case "a": case "A": return "approve";
    case "e": case "E": return "title";
    case "t": case "T": return "tags";
    case "?": return "help";
    default: return null;
  }
}

/** The listing to go to: ``step`` from ``current`` (-1 for none yet), kept inside the list. */
export function move(current: number, step: 1 | -1, count: number): number {
  if (count === 0) return -1;
  if (current < 0) return step > 0 ? 0 : count - 1;
  return Math.min(count - 1, Math.max(0, current + step));
}

/** "$25.00", "€19.50", or the amount and its currency code when the browser has no symbol for it. */
export function formatPrice(amount: number, currency: string | null | undefined): string {
  try {
    return new Intl.NumberFormat("en-US", { style: "currency", currency: currency || "USD" }).format(amount);
  } catch {
    return `${amount.toFixed(2)} ${currency ?? ""}`.trim();
  }
}
