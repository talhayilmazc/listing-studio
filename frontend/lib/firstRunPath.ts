/**
 * The sample path a new seller can walk (app/(app)/first-run): the steps, and what is remembered. The sample's words and
 * pictures come from the server (backend/app/pipeline/first_run.py); nothing on this path writes anything.
 */
import { IMAGE_VERSION_FOR_SAMPLE } from "./sampleVersion.ts";

export const SAMPLE_STEPS = [
  { key: "design", title: "A design", next: "Use this design" },
  { key: "template", title: "A mockup template", next: "Looks right" },
  { key: "mockups", title: "Mockups", next: "Write the listing" },
  { key: "text", title: "Title and tags", next: "See the draft" },
  { key: "draft", title: "The draft", next: null },
] as const;

export type SampleStepKey = (typeof SAMPLE_STEPS)[number]["key"];

export type Sample = {
  version: string; title: string; tags: string[]; description: string; materials: string[]; price: string; currency: string;
  attributes: { name: string; value: string }[]; section: string; colours: string[]; area: number[][];
};

/** The one sentence the last step leads with: what did and did not happen. */
export const NOTHING_SENT = "A preview only. Nothing has gone to Etsy, and this sample never will.";
export const WRITTEN_AHEAD = "Written in advance for this sample. On your own design, the AI writes this for you to change, or you write it yourself.";

export const sampleImage = (name: string, version = IMAGE_VERSION_FOR_SAMPLE): string =>
  `/api/first-run/image?name=${encodeURIComponent(name)}&v=${encodeURIComponent(version)}`;

/** Where the print area sits on the blank, as CSS percentages. */
export function areaBox(area: number[][]): { left: string; top: string; width: string; height: string } {
  const xs = area.map(([x]) => x);
  const ys = area.map(([, y]) => y);
  const pct = (n: number) => `${Math.round(n * 1000) / 10}%`;
  return { left: pct(Math.min(...xs)), top: pct(Math.min(...ys)), width: pct(Math.max(...xs) - Math.min(...xs)), height: pct(Math.max(...ys) - Math.min(...ys)) };
}

export const DONE_KEY = "listyro.sample.done";

export function sampleDoneHere(): boolean {
  try {
    return globalThis.localStorage?.getItem(DONE_KEY) === "1";
  } catch {
    return false;
  }
}

export function rememberSampleDone(): void {
  try {
    globalThis.localStorage?.setItem(DONE_KEY, "1");
  } catch {
    /* not remembered: the guide offers the sample again */
  }
}

/** What comes after the preview: the seller's own first real step, which the account's own data decides. */
export function afterSample(shops: number): { href: string; label: string; line: string } {
  return shops > 0
    ? { href: "/upload", label: "Upload your own design", line: "Your shop is connected. Your own design goes through the same steps, and nothing reaches Etsy until you approve it." }
    : { href: "/connect", label: "Connect a shop", line: "Connect a shop to start on your own designs. Nothing reaches Etsy until you approve it." };
}
