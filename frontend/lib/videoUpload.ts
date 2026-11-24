/**
 * A listing's video on its way up (feature/listing-video): opened, sent in the server's 8 MB pieces (the same
 * resumable way a Photoshop mockup goes: lib/psdUpload.ts), then read by the worker. A piece that fails is
 * sent again by itself; a file that stops half way goes on from where the server has it.
 */
import { ApiError, api, putVideoPiece } from "./api";
import { RETRIES, pieces } from "./psdUpload";
import type { ListingVideo } from "./videoRules";

export interface VideoTarget {
  /** The listing group ("" is the root group); or, with `place`, the folder the file came in. */
  groupKey?: string;
  uploadFolder?: string | null;
  place?: boolean;
  shopId?: string | null;
}

/** Send `file` for a listing. `resume`: a video the server already has part of (the same file, tried again). */
export async function uploadVideo(
  batchId: string, file: File, target: VideoTarget, onProgress: (sent: number, total: number) => void, resume?: ListingVideo | null,
): Promise<ListingVideo> {
  let current = resume && resume.status === "receiving" && resume.size === file.size
    ? await api.video(resume.id)
    : await api.openVideo(batchId, {
      filename: file.name, size: file.size, group_key: target.groupKey ?? null, upload_folder: target.uploadFolder ?? null,
      place: Boolean(target.place), connection_id: target.shopId ?? null,
    });
  onProgress(current.received, file.size);
  for (const piece of pieces(current.received, file.size, current.chunk_size)) {
    let tries = 0;
    for (;;) {
      try {
        current = await putVideoPiece(current.id, piece.offset, file.slice(piece.offset, piece.end), (sent) => onProgress(piece.offset + sent, file.size));
        break;
      } catch (e) {
        // A dropped connection, a busy server or a gap is tried again; a refusal is the answer.
        if (!(e instanceof ApiError) || (e.status !== 0 && e.status !== 409 && e.status < 500) || tries >= RETRIES) throw e;
        tries += 1;
        await new Promise((r) => setTimeout(r, 800 * tries));
      }
    }
  }
  return current;
}
