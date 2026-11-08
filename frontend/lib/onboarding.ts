/**
 * The first-run checklist's view (components/SetupChecklist.tsx). The steps, their words and
 * whether each is done come from the server (GET /api/onboarding; backend pipeline/onboarding.py,
 * ticked from what the seller did, never from a click here). This file only decides what the card
 * shows: the four steps, the "You're set up" note once they are done, or nothing once that note
 * is dismissed.
 */
import type { Onboarding, OnboardingStep } from "./types";

export type View = "steps" | "set_up" | "none";

export function viewOf(o: Onboarding | null): View {
  if (o === null) return "none";
  if (!o.all_done) return "steps";
  return o.dismissed ? "none" : "set_up";
}

/** "2 of 4 done". */
export function doneLine(o: Onboarding): string {
  const done = o.steps.filter((s) => s.done).length;
  return `${done} of ${o.steps.length} done`;
}

/** Each step's look: done, the one to do now, or later. */
export function stateOf(step: OnboardingStep, o: Onboarding): "done" | "now" | "later" {
  if (step.done) return "done";
  return step.key === o.next ? "now" : "later";
}

/** The example designs are offered where a first design is asked for: a seller with no design ready. */
export function offersExamples(o: Onboarding): boolean {
  const upload = o.steps.find((s) => s.key === "upload");
  return Boolean(upload && !upload.done);
}

export const SET_UP = "You're set up. Upload a batch whenever you have new designs.";
export const EXAMPLES_LINE = "No design ready? See three example listings Listyro wrote. They're free and never go to Etsy.";
