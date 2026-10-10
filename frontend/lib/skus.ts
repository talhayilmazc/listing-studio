/**
 * A listing's SKU against Etsy's rules, as the server checks it (backend
 * app/pipeline/skus.py): spaces at either end trimmed, at most 32 characters,
 * no ^, $ or backtick. Said while typing; the server decides.
 */
export const SKU_MAX = 32;
const FORBIDDEN = /[\^$`\u0000-\u001f]/;

export function skuProblem(raw: string): string | null {
  const sku = raw.trim();
  if (!sku) return "Enter a SKU.";
  if (sku.length > SKU_MAX) return `A SKU can be at most ${SKU_MAX} characters on Etsy (${sku.length} now).`;
  if (FORBIDDEN.test(sku)) return "Etsy does not accept ^, $, ` or control characters in a SKU.";
  return null;
}

/** What "Set SKU for selected" makes of one listing's SKU. */
export function withAffixes(current: string | null, sku: string, prefix: string, suffix: string): string {
  return `${prefix}${sku.trim() || (current ?? "")}${suffix}`;
}
