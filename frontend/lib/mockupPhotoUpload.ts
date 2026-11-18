/**
 * Sending a blank photo into a mockup template (backend api/mockups.py: the photo routes).
 *
 * On production a seller chose five photos; four arrived, the fifth was cut on the way (one request per file,
 * tens of megabytes) and the row sat at "100%" with no word. So:
 *
 * - a photo over 8 MB goes up in 8 MB pieces, each tried again by itself, as a Photoshop mockup does
 *   (lib/psdUpload.ts); a smaller one in a single request;
 * - the row never stays at 100%: once the bytes are sent it says "Processing…";
 * - no answer 60 seconds after the last byte, or an answer that is not a success, ends the wait:
 *   "Couldn't upload <file>. Try again", and Try again sends that file only;
 * - at most two photos at a time.
 *
 * Every file carries the page's own id (`upload`): the server answers a repeat of it with the photo it already
 * became, so trying again never adds a photo twice. The file is sent exactly as chosen.
 */
import { messageFor } from "./apiErrors.ts";
import type { MockupPhoto } from "./types";

/** A failed upload: `status` 0 when nothing answered; `body` the server's answer, when it gave one. */
export class UploadError extends Error {
  status: number;
  body?: unknown;
  constructor(message: string, status: number, body?: unknown) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

export const PIECE_BYTES = 8 * 1024 * 1024;
/** Photos sent at the same time. */
export const PHOTOS_AT_ONCE = 2;
/** Tries per piece, the first included. */
export const PIECE_TRIES = 4;
/** How long the page waits for an answer after the last byte left, and for the next byte while sending. */
export const ANSWER_WAIT_MS = 60_000;

export type Phase = "sending" | "processing";

/** "Couldn't upload shirt-black.jpg. Try again" */
export const couldNot = (filename: string): string => `Couldn't upload ${filename}. Try again`;

/** What the row says while a photo is on its way: never "100%". */
export function progressLine(phase: Phase, pct: number): string {
  return phase === "processing" ? "Processing…" : `Uploading ${Math.max(0, Math.min(99, Math.floor(pct)))}%`;
}

/** Whether this file goes up in pieces. */
export const inPieces = (size: number): boolean => size > PIECE_BYTES;

/** The pieces still to send, from where the server says the file ends. */
export function pieces(received: number, total: number, piece = PIECE_BYTES): { offset: number; end: number }[] {
  const out: { offset: number; end: number }[] = [];
  for (let offset = Math.max(0, received); offset < total; offset += piece) out.push({ offset, end: Math.min(total, offset + piece) });
  return out;
}

/** Statuses worth sending the same thing again by itself: no answer, a timeout, a busy server or its proxy. */
const AGAIN = new Set([0, 408, 425, 429, 500, 502, 503, 504]);
export const worthAnother = (status: number): boolean => AGAIN.has(status);

/** A new id for one file (kept for its retries). */
export function newUploadId(): string {
  const c = globalThis.crypto;
  if (c?.randomUUID) return c.randomUUID();
  const b = new Uint8Array(16);
  c.getRandomValues(b);
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

type Sent = { status: number; body: unknown };

/**
 * One request with upload progress. It ends by itself when nothing moves: no byte sent for ANSWER_WAIT_MS, or no
 * answer ANSWER_WAIT_MS after the last byte (status 0, "no answer").
 */
function request(method: "POST" | "PUT", url: string, body: FormData | Blob | string, on: {
  sent?: (loaded: number, total: number) => void; allSent?: () => void;
}, wait = ANSWER_WAIT_MS): Promise<Sent> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const arm = () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => xhr.abort(), wait);
    };
    const done = () => { if (timer) clearTimeout(timer); };
    xhr.open(method, url);
    if (typeof body === "string") xhr.setRequestHeader("Content-Type", "application/json");
    else if (body instanceof Blob) xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.upload.onprogress = (e) => { arm(); on.sent?.(e.loaded, e.total); };
    xhr.upload.onload = () => { arm(); on.allSent?.(); };
    xhr.onload = () => {
      done();
      let parsed: unknown;
      try { parsed = JSON.parse(xhr.responseText); } catch { parsed = undefined; }
      if (xhr.status >= 200 && xhr.status < 300) resolve({ status: xhr.status, body: parsed });
      else reject(new UploadError(messageFor(xhr.status, parsed, xhr.statusText), xhr.status, parsed));
    };
    const lost = (message: string) => () => { done(); reject(new UploadError(message, 0)); };
    xhr.onerror = lost("The upload could not reach the server.");
    xhr.onabort = lost("The server did not answer.");
    arm();
    xhr.send(body);
  });
}

const pause = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * Send one photo into a template. `on` is told where it stands ("sending" with a percentage, then "processing").
 * It resolves with the photo, or throws an UploadError.
 */
export async function sendPhoto(
  templateId: string, file: File, name: string, upload: string, on: (phase: Phase, pct: number) => void,
  opts: { piece?: number; wait?: number; gap?: (n: number) => number } = {},
): Promise<MockupPhoto> {
  const piece = opts.piece ?? PIECE_BYTES;
  const wait = opts.wait ?? ANSWER_WAIT_MS;
  const gap = opts.gap ?? ((n: number) => 1000 * 2 ** (n - 2));
  const base = `/api/mockup-templates/${templateId}/photos`;
  on("sending", 0);
  if (file.size <= piece) {
    const form = new FormData();
    form.append("file", file, file.name);
    form.append("name", name);
    form.append("rights_confirmed", "true");
    form.append("upload", upload);
    const answer = await request("POST", base, form, {
      sent: (loaded, total) => on("sending", total > 0 ? (loaded / total) * 100 : 0),
      allSent: () => on("processing", 100),
    }, wait);
    return answer.body as MockupPhoto;
  }
  let received = 0;
  while (received < file.size) {
    const [next] = pieces(received, file.size, piece);
    const start = received;
    for (let n = 1; ; n += 1) {
      try {
        const answer = await request("PUT", `${base}?upload=${upload}&offset=${next.offset}&size=${file.size}`,
          file.slice(next.offset, next.end), { sent: (loaded) => on("sending", ((start + loaded) / file.size) * 100) }, wait);
        received = (answer.body as { received: number }).received;
        break;
      } catch (e) {
        const error = e as UploadError;
        const held = (error.body as { detail?: { received?: number } } | undefined)?.detail?.received;
        if (error.status === 409 && typeof held === "number") {  // the server holds less than we thought: go on from there
          received = held;
          break;
        }
        if (n >= PIECE_TRIES || !worthAnother(error.status)) throw e;
        await pause(gap(n + 1));
      }
    }
  }
  on("processing", 100);
  const answer = await request("POST", `${base}/assembled`,
    JSON.stringify({ upload, filename: file.name, name, rights_confirmed: true }), {}, wait);
  return answer.body as MockupPhoto;
}
