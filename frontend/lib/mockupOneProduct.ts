/**
 * "This photo has one product" (backend api/mockups.py: POST /api/mockup-photos/{id}/one-product).
 *
 * The analysis's areas are a first suggestion; the seller has the final say, in one click. A photo of one shirt
 * that was given several areas (a fold, a shadow, a necklace or a hat read as another garment) becomes one
 * area on the one garment; the photo's layout is "One garment" by the seller's choice. A later analysis never
 * changes either. The area can still be moved, resized or replaced by hand in the editor.
 */
import { messageFor } from "./apiErrors.ts";
import type { MockupPhoto } from "./types";

export const ONE_PRODUCT = "This photo has one product";
export const ONE_PRODUCT_HINT = "One click: every area on this photo becomes one, on the one garment. You can still move and resize it.";

/** Offered where it changes something: more than one area, or a layout that is not one garment. */
export function offersOneProduct(photo: { status?: string; psd?: unknown; areas?: unknown[]; layout?: string | null }): boolean {
  if (photo.status !== "ready" || photo.psd) return false;
  return (photo.areas?.length ?? 0) > 1 || (photo.layout ?? "single") !== "single";
}

/** The photo with its one area; throws an Error with the server's own words. */
export async function oneProduct(photoId: string): Promise<MockupPhoto> {
  const answer = await fetch(`/api/mockup-photos/${photoId}/one-product`, { method: "POST", credentials: "include" });
  let body: unknown;
  try { body = await answer.json(); } catch { body = undefined; }
  if (!answer.ok) throw new Error(messageFor(answer.status, body, answer.statusText));
  return body as MockupPhoto;
}
