// Folders staged on the upload page before anything is sent. Nothing ever blocks the
// upload (A5, 2026-10-22):
//
// - a folder added again under the same path is merged into it by itself: its new files
//   are added, a file with exactly the same content as one already there is skipped, and
//   a changed file under a name already there is kept as another photo of that listing;
// - two different folders are never the same folder, whatever their names or SKUs
//   ("BR6343-COMFORT" and "br6343" are two folders);
// - loose files from separate "Add photos" picks join the loose files; only identical
//   content is skipped;
// - one line says what happened: "3 photos added to BR6343-COMFORT, 2 identical skipped".
//
// "The same content" is compared by SHA-256, and only for files of the same size.

export interface FileLike {
  name: string;
  size: number;
  lastModified: number;
}

export interface StagedFile<F extends FileLike = FileLike> {
  file: F;
  /** Path inside what was chosen, e.g. "Designs/BR5475/front.png". */
  relpath: string;
}

export interface StagedGroup<F extends FileLike = FileLike> {
  /** The folder (listing group); "" for files at the top level. */
  key: string;
  files: StagedFile<F>[];
}

/** A file's content fingerprint; tests pass their own. */
export type HashOf<F extends FileLike> = (file: F) => Promise<string>;

/** SHA-256 of a browser File's bytes (lowercase hex). */
export async function sha256(file: FileLike): Promise<string> {
  const data = await (file as unknown as Blob).arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

/** The folder a file belongs to; "" for top-level files (D1). */
export function groupKeyOf(relpath: string): string {
  const i = relpath.lastIndexOf("/");
  return i >= 0 ? relpath.slice(0, i) : "";
}

/** "front.png" taken already in that folder: "front (2).png", then "(3)"... */
function freeName(relpath: string, taken: Set<string>): string {
  if (!taken.has(relpath)) return relpath;
  const slash = relpath.lastIndexOf("/");
  const dir = slash >= 0 ? relpath.slice(0, slash + 1) : "";
  const name = relpath.slice(slash + 1);
  const dot = name.lastIndexOf(".");
  const [stem, ext] = dot > 0 ? [name.slice(0, dot), name.slice(dot)] : [name, ""];
  for (let n = 2; ; n += 1) {
    const candidate = `${dir}${stem} (${n})${ext}`;
    if (!taken.has(candidate)) return candidate;
  }
}

export interface StageResult<F extends FileLike> {
  groups: StagedGroup<F>[];
  /** One line per folder that was added to again, e.g. "3 photos added to BR6343-COMFORT, 2 identical skipped". */
  notes: string[];
}

function line(key: string, added: number, skipped: number): string | null {
  const where = key === "" ? "the photos without a folder" : key;
  const photos = (n: number) => `${n} photo${n === 1 ? "" : "s"}`;
  if (added && skipped) return `${photos(added)} added to ${where}, ${skipped} identical skipped`;
  if (added) return `${photos(added)} added to ${where}`;
  if (skipped) return `${where}: ${photos(skipped)} identical to ones already here, skipped`;
  return null;
}

/** Add files: new folders are appended; files for a folder already here are merged into it. */
export async function stage<F extends FileLike>(
  groups: StagedGroup<F>[],
  incoming: StagedFile<F>[],
  hashOf: HashOf<F> = sha256,
): Promise<StageResult<F>> {
  const buckets = new Map<string, StagedFile<F>[]>();
  for (const f of incoming) {
    const key = groupKeyOf(f.relpath);
    buckets.set(key, [...(buckets.get(key) ?? []), f]);
  }
  const next = groups.map((g) => ({ ...g, files: [...g.files] }));
  const notes: string[] = [];
  const hashes = new Map<F, Promise<string>>();
  const hash = (file: F) => {
    if (!hashes.has(file)) hashes.set(file, hashOf(file));
    return hashes.get(file)!;
  };
  for (const [key, files] of buckets) {
    let group = next.find((g) => g.key === key);
    const existed = group !== undefined;
    if (!group) {
      group = { key, files: [] };
      next.push(group);
    }
    let added = 0;
    let skipped = 0;
    for (const f of files) {
      // Identical content (same size first, then the same bytes) is the same photo.
      const sameSize = group.files.filter((e) => e.file.size === f.file.size);
      let identical = false;
      for (const e of sameSize) {
        if (e.file === f.file || (await hash(e.file)) === (await hash(f.file))) {
          identical = true;
          break;
        }
      }
      if (identical) {
        skipped += 1;
        continue;
      }
      // A changed file under a name already here is another photo of the listing.
      const relpath = freeName(f.relpath, new Set(group.files.map((e) => e.relpath)));
      group.files.push({ file: f.file, relpath });
      added += 1;
    }
    if (existed || skipped) {
      const said = line(key, existed ? added : 0, skipped);
      if (said) notes.push(said);
    }
  }
  return { groups: next.filter((g) => g.files.length > 0), notes };
}

export function removeGroup<F extends FileLike>(groups: StagedGroup<F>[], key: string): StagedGroup<F>[] {
  return groups.filter((g) => g.key !== key);
}

/** Staged archives: a second copy of the very same ZIP is not added twice. */
export function stageArchives<F extends FileLike>(archives: F[], incoming: F[]): F[] {
  const out = [...archives];
  for (const z of incoming) {
    if (!out.some((a) => a.name === z.name && a.size === z.size && a.lastModified === z.lastModified)) {
      out.push(z);
    }
  }
  return out;
}

// --- Arranging photos into listings before the upload (B1) -----------------------------------

/** The grouping rule (lib/grouping.ts ``place``), passed in: this module imports nothing. */
export type GroupingMode = "folder" | "sku" | "one";
export type Place = (folder: string | null | undefined, filename: string, mode: GroupingMode | null) => [string, string | null];
const ROOT_KEY = "";
export const UNSORTED_KEY = "~unsorted";
const isUnsorted = (key: string) => key === UNSORTED_KEY;

export interface StagedListing<F extends FileLike = FileLike> {
  /** The listing's group key as the server will make it ("" = the loose photos' listing,
   * UNSORTED_KEY = photos no listing takes yet). */
  key: string;
  files: StagedFile<F>[];
}

function basename(relpath: string): string {
  return relpath.slice(relpath.lastIndexOf("/") + 1);
}

/** The listings the staged photos become in this mode, in the order they appear;
 * Unsorted last. The cards on the upload page are exactly these. */
export function listingsOf<F extends FileLike>(groups: StagedGroup<F>[], mode: GroupingMode, place: Place): StagedListing<F>[] {
  const out = new Map<string, StagedFile<F>[]>();
  for (const g of groups) {
    for (const sf of g.files) {
      const [key] = place(groupKeyOf(sf.relpath), basename(sf.relpath), mode);
      out.set(key, [...(out.get(key) ?? []), sf]);
    }
  }
  const listings = [...out].map(([key, files]) => ({ key, files }));
  return [...listings.filter((l) => !isUnsorted(l.key)), ...listings.filter((l) => isUnsorted(l.key))];
}

/** Arranged by hand: the listings of this mode written down as folders (one listing per
 * folder from now on), so a move is exactly what the seller sees. Photos no listing takes
 * stay loose. */
export function arranged<F extends FileLike>(groups: StagedGroup<F>[], mode: GroupingMode, place: Place): StagedGroup<F>[] {
  const next: StagedGroup<F>[] = [];
  for (const listing of listingsOf(groups, mode, place)) {
    const folder = listing.key === ROOT_KEY || isUnsorted(listing.key) ? "" : listing.key;
    const taken = new Set<string>();
    const files = listing.files.map((sf) => {
      const relpath = freeName(folder ? `${folder}/${basename(sf.relpath)}` : basename(sf.relpath), taken);
      taken.add(relpath);
      return { file: sf.file, relpath };
    });
    const existing = next.find((g) => g.key === folder);
    if (existing) existing.files.push(...files);
    else next.push({ key: folder, files });
  }
  return next;
}

/** A name for a new listing that no card has yet: "New listing", "New listing 2"... */
export function newListingKey<F extends FileLike>(groups: StagedGroup<F>[]): string {
  const taken = new Set(groups.map((g) => g.key));
  for (let n = 1; ; n += 1) {
    const key = n === 1 ? "New listing" : `New listing ${n}`;
    if (!taken.has(key)) return key;
  }
}

/** Move photos onto a listing (``to``: its key; null = a new listing), as arranged by hand.
 * Returns the new groups and the key the photos went to. */
export function movePhotos<F extends FileLike>(
  groups: StagedGroup<F>[], mode: GroupingMode, place: Place, files: F[], to: string | null,
): { groups: StagedGroup<F>[]; key: string } {
  const base = arranged(groups, mode, place);
  const moving = new Set(files);
  const target = to === null ? newListingKey(base) : to === ROOT_KEY || isUnsorted(to) ? "" : to;
  const picked: StagedFile<F>[] = [];
  let next = base.map((g) => {
    const keep = g.files.filter((sf) => !moving.has(sf.file));
    picked.push(...g.files.filter((sf) => moving.has(sf.file)));
    return { ...g, files: keep };
  });
  let group = next.find((g) => g.key === target);
  if (!group) {
    group = { key: target, files: [] };
    next.push(group);
  }
  const taken = new Set(group.files.map((sf) => sf.relpath));
  for (const sf of picked) {
    const relpath = freeName(target ? `${target}/${basename(sf.relpath)}` : basename(sf.relpath), taken);
    taken.add(relpath);
    group.files.push({ file: sf.file, relpath });
  }
  next = next.filter((g) => g.files.length > 0);
  return { groups: next, key: target };
}

/** New files dropped on a listing (or on "+ New listing" with ``to`` null): their paths
 * are put under that listing's folder, then staged as usual (identical content skipped). */
export function underListing<F extends FileLike>(
  groups: StagedGroup<F>[], mode: GroupingMode, place: Place, incoming: StagedFile<F>[], to: string | null,
): { base: StagedGroup<F>[]; incoming: StagedFile<F>[]; key: string } {
  const base = arranged(groups, mode, place);
  const key = to === null ? newListingKey(base) : to === ROOT_KEY || isUnsorted(to) ? "" : to;
  return {
    base,
    key,
    incoming: incoming.map((sf) => ({ file: sf.file, relpath: key ? `${key}/${basename(sf.relpath)}` : basename(sf.relpath) })),
  };
}


/** The photos of one listing card taken out (nothing else rearranged). */
export function withoutListing<F extends FileLike>(groups: StagedGroup<F>[], mode: GroupingMode, place: Place, key: string): StagedGroup<F>[] {
  const out = listingsOf(groups, mode, place).find((l) => l.key === key);
  const gone = new Set((out?.files ?? []).map((sf) => sf.file));
  return groups.map((g) => ({ ...g, files: g.files.filter((sf) => !gone.has(sf.file)) })).filter((g) => g.files.length > 0);
}
