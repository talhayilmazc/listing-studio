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
