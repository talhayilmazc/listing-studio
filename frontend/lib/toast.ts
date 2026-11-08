/**
 * Short notes at the foot of the screen (components/Toaster.tsx): "Approved" with Undo, "Listings
 * ready" with a link, wherever the seller is. An action keeps its name: the button "Approve" gives
 * the note "Approved" (lib/words.ts). A reversible action gives an Undo note instead of asking
 * first; only what cannot be undone asks (app/onboarding-v1).
 *
 * Anything can raise one with ``toast(...)``; the Toaster in the app frame shows them, newest last,
 * at most ``MAX`` at once, each for ``seconds`` (longer when it offers Undo).
 */

export type Toast = {
  id: number;
  message: string;
  /** What the note's button does: Undo, or go somewhere. */
  action?: { label: string; run?: () => void | Promise<void>; href?: string };
  /** "error" notes stay until closed. */
  tone?: "done" | "error";
  seconds: number;
};

export const TOAST_EVENT = "listyro:toast";
export const MAX = 3;
export const SECONDS = 6;
export const UNDO_SECONDS = 10;

let next = 1;

export function makeToast(message: string, options: Omit<Partial<Toast>, "id" | "message"> = {}): Toast {
  const undo = options.action?.label === "Undo";
  return {
    id: next++,
    message,
    action: options.action,
    tone: options.tone ?? "done",
    seconds: options.seconds ?? (options.tone === "error" ? 0 : undo ? UNDO_SECONDS : SECONDS),
  };
}

/** Raise a note. In a test or on the server (no window) it is returned and nothing else happens. */
export function toast(message: string, options: Omit<Partial<Toast>, "id" | "message"> = {}): Toast {
  const t = makeToast(message, options);
  if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent(TOAST_EVENT, { detail: t }));
  return t;
}

/** The notes on screen after ``t`` arrives: newest last, the oldest dropped past ``MAX``. */
export function push(list: Toast[], t: Toast): Toast[] {
  return [...list.filter((x) => x.id !== t.id), t].slice(-MAX);
}

export function drop(list: Toast[], id: number): Toast[] {
  return list.filter((x) => x.id !== id);
}

/** "Approved", "Approved 3": the note for an action done to ``count`` things. */
export function doneNote(word: string, count = 1): string {
  return count === 1 ? word : `${word} ${count}`;
}
