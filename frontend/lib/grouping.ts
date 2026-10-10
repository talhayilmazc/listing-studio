/**
 * How uploaded photos become listings. A TypeScript port of the server's rules
 * (backend app/pipeline/grouping.py and the default SKU parser in sku.py), so the
 * upload page can preview "42 listings, 3 photos unsorted" before the files are
 * sent. The server decides; both test suites read the same cases
 * (backend/tests/fixtures/grouping_cases.json), so the two never disagree.
 *
 * The seller's choice ("How should these photos become listings?"):
 *   folder  one listing per folder; files in a folder are never split; loose files are one listing
 *   sku     every photo by the SKU in its file name; a photo with none takes its folder's SKU, else Unsorted
 *   one     all these photos are one listing
 */
export const UNSORTED_KEY = "~unsorted";
export const ROOT_KEY = "";

export type GroupingMode = "folder" | "sku" | "one";
export const MODES: GroupingMode[] = ["folder", "sku", "one"];

export const MODE_LABELS: Record<GroupingMode, { label: string; detail: string }> = {
  folder: { label: "One listing per folder", detail: "Each folder becomes a listing. Files inside a folder are never split, whatever their names." },
  sku: { label: "Group by SKU in the file name", detail: "BR5229-1.png and BR5229 copy.png become one listing. Photos with no SKU in their name wait in Unsorted." },
  one: { label: "All these photos are one listing", detail: "Every photo goes into a single listing." },
};

export const isUnsorted = (key: string | null | undefined): boolean => key === UNSORTED_KEY;

/** What a group is called on screen. */
export function groupLabel(key: string | null | undefined): string {
  if (isUnsorted(key)) return "Unsorted";
  if (!key || key === "(root)") return "Root folder";
  return key;
}

// --- The SKU parser's default rules (backend app/pipeline/sku.py) --------------------
const VIEWS = "front|back|main|side|top|bottom|detail|closeup|angle|thumb|hero|left|right";
const RULES = [
  /(?<sku>[A-Za-z]{2,4}\d{3,6})(?:[-_]\d+)?$/i,
  new RegExp(`^(?<sku>.+?)[-_](?:${VIEWS})\\b`, "i"),
  /^(?<sku>.+?)[-_]\d{1,3}$/i,
  /^(?<sku>[A-Za-z0-9][A-Za-z0-9\-_]*)$/i,
];
const GROUP_SKU = /[A-Za-z]{2,4}\d{3,6}/;
const CLEAN_TOKEN = /^[A-Za-z0-9][A-Za-z0-9\-_]*$/;

function stripExtension(name: string): string {
  const base = name.replace(/\\/g, "/").split("/").pop() ?? name;
  const dot = base.lastIndexOf(".");
  return dot > 0 ? base.slice(0, dot) : base;
}

/** SkuParser.parse: the first rule that matches, upper-cased. */
export function parseSku(stem: string): string | null {
  const name = stem.trim();
  for (const rule of RULES) {
    const m = rule.exec(name);
    const sku = m?.groups?.sku ?? m?.[0];
    if (sku) return sku.trim().toUpperCase();
  }
  return null;
}

/** SkuParser.parse_group: the SKU in a folder's name. */
export function folderSku(folder: string | null | undefined): string | null {
  if (!folder) return null;
  const name = folder.replace(/\\/g, "/").replace(/\/+$/, "").split("/").pop()?.trim() ?? "";
  if (!name) return null;
  const m = GROUP_SKU.exec(name);
  if (m) return m[0].toUpperCase();
  return CLEAN_TOKEN.test(name) ? name.toUpperCase() : null;
}

// --- grouping.py ---------------------------------------------------------------------
const SUFFIXES = [
  /\s*\(\d+\)$/,
  /[\s_-]+copy(?:[\s_-]*\d+)?$/i,
  /[\s_-]+(?:front|back|mockup|mock|main|side|detail|closeup|flat|lay|model|preview|thumb|hero)$/i,
  /[\s_-]+\d{1,3}$/,
];
const DEVICE =
  /^(?:img|dsc|dscn|dscf|dcim|pxl|mvimg|vid|photo|image|pic|screenshot|screen[\s_-]?shot|whatsapp[\s_-]?image|untitled|scan|capture)\b|^(?:img|dsc|pxl|p)[\s_-]?\d/i;
const TOKEN = /^[A-Za-z0-9][A-Za-z0-9\-_]*$/;
// Names people give photos one at a time ("design1.png", "photo 2.jpg"): a word and
// a count would split one design's photos into as many listings; not a SKU.
const COUNTED =
  /^(?:design|photo|image|picture|pic|mockup|mock|file|new|final|sample|test|draft|version|page|slide|frame|copy|shirt|tshirt|t-shirt|tee|item|product)[\s_-]?\d+$|^[A-Za-z]{5,}[\s_-]?\d{1,2}$/i;
const isSku = (text: string) => /\d/.test(text) && /[A-Za-z]/.test(text) && !COUNTED.test(text);

export function normalisedStem(filename: string): string {
  let stem = stripExtension(filename).trim();
  let changed = true;
  while (changed && stem) {
    changed = false;
    for (const pattern of SUFFIXES) {
      const shorter = stem.replace(pattern, "").trim();
      if (shorter !== stem && shorter) {
        stem = shorter;
        changed = true;
      }
    }
  }
  return stem;
}

/** The SKU a loose file is grouped by, or null (it goes to Unsorted). */
export function skuOf(filename: string): string | null {
  const stem = normalisedStem(filename);
  if (!stem || DEVICE.test(stem)) return null;
  let sku = parseSku(stem);
  if (sku) {
    const at = stem.toUpperCase().indexOf(sku.toUpperCase());
    if (at > 0 && /[A-Za-z0-9]/.test(stem[at - 1])) sku = TOKEN.test(stem) ? stem : null;
  }
  return sku && isSku(sku) ? sku.toUpperCase() : null;
}

function folderKey(folder: string | null | undefined): string | null {
  if (folder === UNSORTED_KEY) return `${UNSORTED_KEY} (folder)`;
  return folder || null;
}

/** [group key, SKU] for one file, as the server will place it. */
export function place(folder: string | null | undefined, filename: string, mode: GroupingMode | null): [string, string | null] {
  const f = folderKey(folder);
  const fSku = f ? folderSku(f) : null;
  if (mode === "folder") return f ? [f, fSku ?? parseSku(stripExtension(filename))] : [ROOT_KEY, parseSku(stripExtension(filename))];
  if (mode === "one") return [ROOT_KEY, skuOf(filename) ?? fSku];
  if (mode === "sku") {
    const sku = skuOf(filename) ?? (fSku && isSku(fSku) ? fSku : null);
    return sku ? [sku, sku] : [UNSORTED_KEY, null];
  }
  if (f) return [f, fSku ?? parseSku(stripExtension(filename))];
  const sku = skuOf(filename);
  return sku ? [sku, sku] : [UNSORTED_KEY, null];
}

export interface PreviewFile {
  /** The folder it is in ("" when loose). */
  folder: string;
  name: string;
}

export interface Preview {
  listings: number;
  unsorted: number;
}

/** How many listings these files make in ``mode``, and how many photos are left unsorted. */
export function preview(files: PreviewFile[], mode: GroupingMode): Preview {
  const keys = new Set<string>();
  let unsorted = 0;
  for (const f of files) {
    const [key] = place(f.folder, f.name, mode);
    if (isUnsorted(key)) unsorted += 1;
    else keys.add(key);
  }
  return { listings: keys.size, unsorted };
}

/** "42 listings, 3 photos unsorted". */
export function previewText(p: Preview): string {
  const head = `${p.listings} listing${p.listings === 1 ? "" : "s"}`;
  return p.unsorted ? `${head}, ${p.unsorted} photo${p.unsorted === 1 ? "" : "s"} unsorted` : head;
}

/** The kind of upload a remembered choice applies to. */
export const uploadKind = (files: PreviewFile[]): "folders" | "flat" => (files.some((f) => f.folder) ? "folders" : "flat");

/**
 * The sensible default: folders present -> one per folder; flat files with SKUs
 * found -> by SKU; flat files with no SKU -> all one listing. A choice the seller
 * made last time for the same kind of upload wins.
 */
export function autoMode(files: PreviewFile[], remembered?: Partial<Record<"folders" | "flat", GroupingMode>>): GroupingMode {
  const kind = uploadKind(files);
  const last = remembered?.[kind];
  if (last && MODES.includes(last)) return last;
  if (kind === "folders") return "folder";
  return files.some((f) => skuOf(f.name)) ? "sku" : "one";
}

// The seller's last choice, per kind of upload; this browser only (a convenience).
const STORE = "listyro.grouping";

export function rememberedChoices(): Partial<Record<"folders" | "flat", GroupingMode>> {
  try {
    const raw = globalThis.localStorage?.getItem(STORE);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

export function rememberChoice(kind: "folders" | "flat", mode: GroupingMode): void {
  try {
    globalThis.localStorage?.setItem(STORE, JSON.stringify({ ...rememberedChoices(), [kind]: mode }));
  } catch {
    /* private window or storage blocked: the default is used next time */
  }
}
