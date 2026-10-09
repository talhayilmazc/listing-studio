/**
 * Listing groups as the server makes them (backend pipeline/grouping.py): a
 * folder is a group; loose files are grouped by the SKU in their file name; a
 * file with no readable SKU waits in the Unsorted tray, which is never written
 * or drafted until its photos are moved into a group (or ignored on purpose).
 */
export const UNSORTED_KEY = "~unsorted";

export const isUnsorted = (key: string | null | undefined): boolean => key === UNSORTED_KEY;

/** What a group is called on screen. */
export function groupLabel(key: string | null | undefined): string {
  if (isUnsorted(key)) return "Unsorted";
  if (!key || key === "(root)") return "Root folder";
  return key;
}
