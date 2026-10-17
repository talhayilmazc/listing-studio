/**
 * Mockup templates: the words and limits the pages share (backend api/mockups.py,
 * pipeline/mockups.py, core/config.py). lib/mockups.test.ts fails if a figure or
 * the rights sentence drifts from the backend's.
 */
import type { MockupPhoto, MockupTemplate } from "./types";

/** What the seller confirms when uploading a blank: the same words the server checks for. */
export const RIGHTS_STATEMENT = "This photo is my own, or I have the right to use it to show my products.";

/** The size setting (share of the print area) and fold strength, as the server allows them. */
export const SIZE_RANGE: [number, number] = [0.2, 1.0];
export const FOLD_RANGE: [number, number] = [0.0, 1.0];
/** The most photos one run prints on (every design on each). */
export const MAX_PHOTOS_PER_RUN = 60;
/** Photos one template holds. */
export const MAX_PHOTOS_PER_TEMPLATE = 40;
/** A blank photo or a design, per file (the server refuses more). */
export const MAX_UPLOAD_MB = 30;
/** A template's or a photo's name. */
export const MAX_NAME = 120;

export const TEMPLATES_INTRO =
  "A template is a group of blank photos: the same garment in each colour, or several shots of one product. Every design is placed on every photo of the template, as if printed on the fabric. Only your design is used: it is fitted, warped to the fabric and shaded by it, never redrawn. Mockups do not count toward Listings generated.";

export const ACCURACY_NOTE =
  "Etsy asks for photos that show the item accurately: use a blank of the garment you actually sell, in its real colour.";

/** On a photo whose print areas the seller has not confirmed yet. */
export const NEEDS_CHECK = "Needs your check";
/** The editor's primary button: saves the areas and marks the photo confirmed. */
export const LOOKS_RIGHT = "Looks right";
/** What the editor says about the suggestion: it is where to start, not the answer. */
export const SUGGESTION_NOTE =
  "We placed the print area where we think it goes. Move it if it is not right, then press Looks right. Photos are used for mockups only after this check.";
export const EDITOR_HINT =
  "Drag inside the area to move it, a corner to resize it, the round handle to turn it. Arrow keys nudge it (Shift for bigger steps). Lines appear when it lines up; hold Alt to place it freely.";
export const OCCLUSION_LABEL = "Put the design behind hair, skin, hands and things held in front";
/** The "Keep in front" brush, said in the editor. */
export const BRUSH_HINT =
  "Paint over hair, hands or anything that must stay in front of the design; erase what should not. Red shows what stays in front. Your brush is kept with this photo and used in every preview and mockup.";
/** Said when a run leaves photos out because they are not confirmed. */
export const skippedLine = (n: number) =>
  `${n} ${n === 1 ? "photo needs" : "photos need"} your check and will not be used until you confirm ${n === 1 ? "it" : "them"}.`;
/** "3 photos need your check" on a template card. */
export const checkLine = (n: number) => `${n} ${n === 1 ? "photo needs" : "photos need"} your check`;

/** The run's choices: which photos (by the side each shows) and how designs fill a photo of several garments. */
export const SIDE_LABELS: Record<"both" | "front" | "back", string> = {
  both: "Front and back photos",
  front: "Front photos only",
  back: "Back photos only",
};
export const GARMENT_MODE_LABELS: Record<"same" | "order", string> = {
  same: "The same design on every garment of a photo",
  order: "One design per garment, in order",
};

export const LIMIT_NOTE = `JPEG, PNG or WebP, up to ${MAX_UPLOAD_MB} MB each. A plain background, or a PNG with a transparent background, works best.`;

export type Corner = [number, number];

/** Why a file cannot be uploaded (said before anything is sent), or null. */
export function sizeProblem(file: { name: string; size: number }): string | null {
  if (file.size <= MAX_UPLOAD_MB * 1024 * 1024) return null;
  return `${file.name} is ${(file.size / (1024 * 1024)).toFixed(1)} MB: the limit is ${MAX_UPLOAD_MB} MB per file.`;
}

/** A photo's name from its file name: "white-tee_front.png" -> "white tee front". */
export function nameFromFile(filename: string): string {
  const stem = filename.replace(/\\/g, "/").split("/").pop()!.replace(/\.[^.]+$/, "");
  return stem.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim().slice(0, MAX_NAME) || "Blank";
}

/** A template's name from several photos: what their names share ("Comfort Colors 1717 White", "… Black" -> "Comfort Colors 1717"). */
export function templateNameFrom(names: string[]): string {
  if (!names.length) return "Template";
  const words = names.map((n) => n.split(/\s+/));
  const shared: string[] = [];
  for (let i = 0; i < words[0].length; i++) {
    if (words.every((w) => w[i]?.toLowerCase() === words[0][i].toLowerCase())) shared.push(words[0][i]);
    else break;
  }
  if (names.length === 1) return names[0];
  return shared.join(" ").trim().slice(0, MAX_NAME) || names[0];
}

/**
 * The photos a run prints on: every confirmed, ready photo of the chosen templates showing
 * the chosen side, in template order then photo order (as the server does,
 * api/mockups.py ``start_run``), and how many of those templates' photos it leaves out
 * because they are not confirmed.
 */
export function printable(
  templates: MockupTemplate[], chosen: string[], sides: "both" | "front" | "back",
): { photos: MockupPhoto[]; skipped: number; skippedNames: string[] } {
  const photos: MockupPhoto[] = [];
  const skippedNames: string[] = [];
  for (const t of templates) {
    if (!chosen.includes(t.id)) continue;
    for (const p of t.photos) {
      if (sides !== "both" && (p.view === "back" ? "back" : "front") !== sides) continue;
      if (p.status === "ready" && p.confirmed) photos.push(p);
      else skippedNames.push(p.name);
    }
  }
  return { photos, skipped: skippedNames.length, skippedNames };
}

/** Where "Add designs" goes: the bulk step with these templates chosen. */
export const addDesignsHref = (templateIds: string[]) =>
  `/templates/designs${templateIds.length ? `?templates=${templateIds.join(",")}` : ""}`;

/** A template's designs can be added once at least one photo is confirmed. */
export const canAddDesigns = (template: MockupTemplate) => template.confirmed > 0;

/** Said right after the last photo that needed a check is confirmed. */
export const ALL_READY = "All photos are ready. Add designs";

/** What the page says about an uploaded design that will print, but not as the seller may expect. */
export function designNote(design: { has_alpha: boolean }): string | null {
  return design.has_alpha ? null : "No transparent background: it is printed as a rectangle.";
}

/** On a template whose suggestions were built from the seller's own confirmed photos. */
export function ownPlacementLine(n: number): string {
  return `Using your own placement from ${n} ${n === 1 ? "photo" : "photos"}`;
}

/** What "Reset" asks before forgetting the seller's placement. */
export const RESET_PLACEMENT =
  "Forget your own placement? New photos are suggested from the standard placement again. Areas already on your photos stay as they are.";

/** "Apply this area to selected" and "Confirm selected", as one line about what happened. */
export function appliedLine(n: number): string {
  return `Placed on ${n} ${n === 1 ? "photo" : "photos"} by each one's own garment. Check them, then press Confirm selected.`;
}

/** The photos of a template still waiting for "Looks right", in order (the editor's queue). */
export function waitingForCheck(template: MockupTemplate): string[] {
  return template.photos.filter((p) => p.needs_check).map((p) => p.id);
}

/** "73%": a setting as the seller reads it. */
export const percent = (v: number) => `${Math.round(v * 100)}%`;

/** Progress of a run, as one line. */
export function runLine(run: { status: string; done: number; total: number; failed: number }): string {
  if (run.status === "queued") return "Waiting to start…";
  if (run.status === "running") return `Making mockups: ${run.done} of ${run.total}`;
  const made = `${run.done} ${run.done === 1 ? "mockup" : "mockups"} made`;
  return run.failed ? `${made}, ${run.failed} could not be made` : made;
}

/** "1.4 MB" */
export const megabytes = (bytes: number) => `${(bytes / (1024 * 1024)).toFixed(1)} MB`;

/**
 * The editor's sample design (a plain drawing of our own, not a real design): shown in the
 * live preview when the seller has not chosen one of theirs. 1000 x 1000.
 */
export const SAMPLE_DESIGN = {
  width: 1000,
  height: 1000,
  url:
    "data:image/svg+xml;utf8," +
    encodeURIComponent(
      '<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="1000" viewBox="0 0 1000 1000">' +
        '<circle cx="500" cy="380" r="300" fill="#f59e0b"/>' +
        '<rect x="120" y="620" width="760" height="150" rx="24" fill="#0f766e"/>' +
        '<text x="500" y="728" font-family="Arial,Helvetica,sans-serif" font-size="110" font-weight="700" fill="#fff" text-anchor="middle">SAMPLE</text>' +
        '<path d="M300 860h400" stroke="#0f766e" stroke-width="40" stroke-linecap="round"/>' +
        "</svg>",
    ),
};
