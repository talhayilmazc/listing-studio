// Many files at once, for template photos and designs (components/FileDrop.tsx): what each
// dropped or chosen file is, and why one cannot be sent, before anything is sent.

/** The per-file limit: the same as lib/mockups.ts MAX_UPLOAD_MB (lib/mockupFlow.test.ts checks). */
export const MAX_UPLOAD_MB = 30;

export type Picked = { file: File; kind: "image" | "zip" | "other"; problem: string | null };

const IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp"];
const IMAGE_EXT = /\.(png|jpe?g|webp)$/i;
const ZIP_EXT = /\.zip$/i;
/** Files a folder or a ZIP picks up that are not anyone's photo. */
const CLUTTER = /(^|\/)(\.ds_store|thumbs\.db|desktop\.ini|__macosx\/.*|\._.*)$/i;

/** What a file is, from its type and name (the server decides again from its bytes). */
export function kindOf(file: { name: string; type: string }): Picked["kind"] {
  if (IMAGE_TYPES.includes(file.type) || IMAGE_EXT.test(file.name)) return "image";
  if (file.type === "application/zip" || file.type === "application/x-zip-compressed" || ZIP_EXT.test(file.name)) return "zip";
  return "other";
}

/** Each file with what it is and why it cannot be sent, or null; clutter left out. */
export function pick(files: File[], { zip = false }: { zip?: boolean } = {}): Picked[] {
  return files
    .filter((f) => !CLUTTER.test(((f as any).webkitRelativePath as string) || f.name))
    .map((file) => {
      const kind = kindOf(file);
      let problem: string | null = null;
      if (kind === "other" || (kind === "zip" && !zip)) {
        problem = `${file.name} is not a photo: use JPEG, PNG or WebP${zip ? ", or a ZIP of them" : ""}.`;
      } else if (kind === "image" && file.size > MAX_UPLOAD_MB * 1024 * 1024) {
        problem = `${file.name} is ${(file.size / (1024 * 1024)).toFixed(1)} MB: the limit is ${MAX_UPLOAD_MB} MB per file.`;
      }
      return { file, kind, problem };
    });
}

/** Every file of a drop, folders opened (a directory reader answers in parts of up to 100). */
export async function filesFromDrop(dt: DataTransfer): Promise<File[]> {
  const entries = Array.from(dt.items ?? [])
    .map((it) => (it.webkitGetAsEntry ? it.webkitGetAsEntry() : null))
    .filter(Boolean) as FileSystemEntry[];
  if (!entries.length) return Array.from(dt.files ?? []);
  const out: File[] = [];
  await Promise.all(entries.map((e) => walk(e, out)));
  return out.sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
}

async function walk(entry: FileSystemEntry, out: File[]): Promise<void> {
  if (entry.isFile) {
    out.push(await new Promise<File>((resolve, reject) => (entry as FileSystemFileEntry).file(resolve, reject)));
    return;
  }
  if (!entry.isDirectory) return;
  const reader = (entry as FileSystemDirectoryEntry).createReader();
  for (;;) {
    const batch = await new Promise<FileSystemEntry[]>((resolve, reject) => reader.readEntries(resolve, reject));
    if (!batch.length) break;
    await Promise.all(batch.map((e) => walk(e, out)));
  }
}
