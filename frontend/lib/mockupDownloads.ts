/**
 * Downloading mockups (feature/mockups-visible, A2; backend pipeline/mockup_download.py): the options' words, the
 * ZIP's status line and what "Download selected" sends. Sellers download and put the files in Google Drive
 * themselves; the app uploads nowhere.
 */
import type { DownloadOptions, MockupZip } from "./types";

export const DEFAULT_OPTIONS: DownloadOptions = { format: "jpg", size: "original", quality: 92, naming: "full", layout: "folders" };

export const FORMAT_LABELS: [DownloadOptions["format"], string][] = [["jpg", "JPG"], ["png", "PNG"], ["webp", "WebP"]];
export const SIZE_LABELS: [DownloadOptions["size"], string][] = [
  ["original", "Original"], ["3000", "3000 px"], ["2000", "2000 px (Etsy)"], ["1080", "1080 px (social)"]];
export const QUALITY_LABELS: [DownloadOptions["quality"], string][] = [[92, "High"], [85, "Smaller"]];
export const NAMING_LABELS: [DownloadOptions["naming"], string][] = [
  ["full", "SKU_template_colour_1"], ["sku", "SKU_1"], ["file", "Design file name_1"]];
export const LAYOUT_LABELS: [DownloadOptions["layout"], string][] = [
  ["folders", "A folder per design"], ["flat", "All in one folder"]];

/** Said once, by the options: what the sizes and the files are. */
export const SIZE_NOTE = "Long side. Mockups are made at up to 2000 px and are never enlarged.";
export const SAME_AS_ETSY =
  "Each file is the mockup as you adjusted it, the same pixels a draft sends to Etsy (a listing's first photo with its crop), in sRGB at 300 DPI, with no camera data.";
export const KEPT_HOURS = 48;

/** The ZIP's one line. */
export function zipLine(zip: Pick<MockupZip, "status" | "files" | "total" | "done" | "error">, megabytes: string | null = null): string {
  switch (zip.status) {
    case "ready":
      return `${zip.files ?? 0} ${zip.files === 1 ? "file" : "files"}${megabytes ? `, ${megabytes}` : ""}. Kept ${KEPT_HOURS} hours, then deleted.`;
    case "failed":
      return zip.error ?? "The ZIP could not be made.";
    case "expired":
      return "This download has expired; make a new one.";
    default:
      return zip.total ? `Making the ZIP: ${zip.done ?? 0} of ${zip.total}` : "Making the ZIP…";
  }
}

/** Output sharpening (B6), in the seller's words. */
export const SHARPEN_LABELS: ["off" | "low" | "medium", string][] = [["off", "Off"], ["low", "Low"], ["medium", "Medium"]];

/** After a pass: how many were kept as they were (B5/B7), or null when none. */
export function keptLine(reused: number | undefined): string | null {
  if (!reused) return null;
  return `${reused} ${reused === 1 ? "mockup was" : "mockups were"} kept as ${reused === 1 ? "it was" : "they were"}: nothing that shows in ${reused === 1 ? "it" : "them"} had changed.`;
}

/** Still being made: the page looks again. */
export const making = (zip: Pick<MockupZip, "status">): boolean => zip.status === "queued" || zip.status === "building";

/** "Download selected (3)": the chosen mockups, or null for none. */
export function selectedLabel(count: number): string {
  return count ? `Download selected (${count})` : "Download selected";
}
