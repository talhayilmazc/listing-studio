// Folders staged on the upload page before anything is sent: added folders are
// appended, never replacing what is already there; a folder added again under
// the same name waits for the seller to merge it or skip it; any folder can be
// removed before the upload starts.

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
  /** The same folder added again, waiting for "Merge" or "Skip". */
  pending: StagedFile<F>[];
}

/** The folder a file belongs to; "" for top-level files (D1). */
export function groupKeyOf(relpath: string): string {
  const i = relpath.lastIndexOf("/");
  return i >= 0 ? relpath.slice(0, i) : "";
}

function sameFile(a: StagedFile, b: StagedFile): boolean {
  return (
    a.relpath === b.relpath &&
    a.file.size === b.file.size &&
    a.file.lastModified === b.file.lastModified
  );
}

/** Add files: new folders are appended; a folder already staged is flagged, not replaced. */
export function stage<F extends FileLike>(groups: StagedGroup<F>[], incoming: StagedFile<F>[]): StagedGroup<F>[] {
  const buckets = new Map<string, StagedFile<F>[]>();
  for (const f of incoming) {
    const key = groupKeyOf(f.relpath);
    buckets.set(key, [...(buckets.get(key) ?? []), f]);
  }
  const next = groups.map((g) => ({ ...g }));
  for (const [key, files] of buckets) {
    const existing = next.find((g) => g.key === key);
    if (!existing) {
      next.push({ key, files, pending: [] });
      continue;
    }
    // Exactly the same files again: nothing to decide.
    const fresh = files.filter((f) => !existing.files.some((e) => sameFile(e, f)));
    if (fresh.length) existing.pending = [...existing.pending, ...fresh];
  }
  return next;
}

/** "Merge": the pending files join the folder (identical ones only once). */
export function mergePending<F extends FileLike>(groups: StagedGroup<F>[], key: string): StagedGroup<F>[] {
  return groups.map((g) => {
    if (g.key !== key) return g;
    const added = g.pending.filter((p) => !g.files.some((f) => sameFile(f, p)));
    return { ...g, files: [...g.files, ...added], pending: [] };
  });
}

/** "Skip": the folder stays as it was. */
export function skipPending<F extends FileLike>(groups: StagedGroup<F>[], key: string): StagedGroup<F>[] {
  return groups.map((g) => (g.key === key ? { ...g, pending: [] } : g));
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
