/**
 * Uploading a Photoshop mockup (feature/psd-templates; backend api/psd_templates.py). The words and the rules a
 * page needs, kept apart from the component so they can be tested.
 *
 * The file goes up in pieces (the server says how big: `chunk_size`, 16 MB). After a lost connection the page
 * asks the server how much arrived and goes on from there; a piece already received is accepted again and changes
 * nothing. The file is never resized or recompressed in the browser.
 */

export const MAX_BYTES = 500 * 1024 * 1024;
export const EXTENSIONS = [".psd", ".psb"];
export const RIGHTS = "I have the right to use this mockup for my listings.";
export const BUTTON = "Upload a Photoshop mockup (PSD)";
export const INTRO =
  "A Photoshop mockup with a smart object where the design goes. Listyro turns it into a template and places each " +
  "design in the smart object, as Photoshop would. The template is yours only.";
export const TOO_BIG =
  "This file is larger than 500 MB. In Photoshop, delete the layers the mockup does not need or save a smaller copy " +
  "(Image > Image Size), then upload it again.";
export const NOT_PSD = "Choose a Photoshop file (.psd or .psb).";
export const KEPT_PRIVATE = "Your templates are private to your account and never shown to anyone else.";
/** How many times one piece is sent again after a dropped connection before the page stops and says so. */
export const RETRIES = 4;

/** Why this file cannot be uploaded, or null. */
export function fileProblem(file: { name: string; size: number }): string | null {
  const name = file.name.toLowerCase();
  if (!EXTENSIONS.some((ext) => name.endsWith(ext))) return NOT_PSD;
  if (file.size > MAX_BYTES) return TOO_BIG;
  if (file.size <= 0) return NOT_PSD;
  return null;
}

/** "120 of 270 MB" */
export function sizeLine(sent: number, total: number): string {
  const mb = (n: number) => (n / (1024 * 1024)).toFixed(n < 10 * 1024 * 1024 ? 1 : 0);
  return `${mb(sent)} of ${mb(total)} MB`;
}

/** Whole percent, never 100 before the last byte. */
export function percent(sent: number, total: number): number {
  if (total <= 0) return 0;
  if (sent >= total) return 100;
  return Math.min(99, Math.floor((sent / total) * 100));
}

/** The pieces still to send, from where the server says the file ends. */
export function pieces(received: number, total: number, chunk: number): { offset: number; end: number }[] {
  const out: { offset: number; end: number }[] = [];
  for (let offset = received; offset < total; offset += chunk) out.push({ offset, end: Math.min(total, offset + chunk) });
  return out;
}

export type PsdStatus = "receiving" | "queued" | "converting" | "ready" | "used" | "failed" | "cancelled";

/** The one line that says where an upload stands. */
export function statusLine(status: PsdStatus, sent: number, total: number): string {
  switch (status) {
    case "receiving":
      return `Uploading: ${percent(sent, total)}% · ${sizeLine(sent, total)}`;
    case "queued":
      return "Uploaded. Waiting to be turned into a template…";
    case "converting":
      return "Reading the layers and the smart object…";
    case "ready":
      return "Check the preview below, then use the template.";
    case "used":
      return "Added to your templates.";
    case "failed":
      return "This file could not be turned into a template.";
    default:
      return "Cancelled.";
  }
}

/** Waiting states the page looks at again until they change. */
export const busy = (status: PsdStatus): boolean => status === "queued" || status === "converting";

/** A seller's file is matched to an unfinished upload by name and size (to continue it, not start again). */
export function sameFile(upload: { filename: string; size: number }, file: { name: string; size: number }): boolean {
  return upload.filename === file.name && upload.size === file.size;
}
