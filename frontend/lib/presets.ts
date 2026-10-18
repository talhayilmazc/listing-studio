// File-name prefixes for a template's mockups (backend pipeline/mockups.py clean_prefix and
// prefixed_name): the same cleaning, so the example the page shows is the name the files get.

export const PREFIX_MAX = 20;

/** A prefix as the server stores it: spaces become "-", only letters, digits, "-" and "_", at most 20. */
export function cleanPrefix(text: string): string | null {
  const cleaned = text.trim().replace(/\s+/g, "-").replace(/[^A-Za-z0-9_-]+/g, "").replace(/^[-_]+|[-_]+$/g, "").slice(0, PREFIX_MAX);
  return cleaned || null;
}

/** "CC-BR100-01.jpg": what the first mockup of a design is named with this prefix. */
export function prefixedExample(prefix: string, sku = "BR100", number = 1): string {
  const p = cleanPrefix(prefix);
  return p ? `${p}-${sku}-${String(number).padStart(2, "0")}.jpg` : "";
}
