/**
 * A listing's video: Etsy's rules as the page checks them before anything is sent (feature/listing-video).
 * The server's copy and where each limit comes from: backend/app/pipeline/video_rules.py. Both read
 * backend/tests/fixtures/video_rule_cases.json; the server is the one that decides.
 */

export const MIN_SECONDS = 5;
export const MAX_SECONDS = 15;
export const SECONDS_TOLERANCE = 0.25;
export const MAX_BYTES = 100 * 1024 * 1024;
export const EXTENSIONS = [".mp4", ".mov"];
export const ACCEPT = ".mp4,.mov,video/mp4,video/quicktime";
export const VIDEO_RE = /\.(mp4|mov)$/i;

export const NOT_A_VIDEO = "Choose an MP4 or MOV video.";
export const UNREADABLE = "This file could not be read as a video. Export it again as MP4 or MOV and add it again.";

export interface ListingVideo {
  id: string;
  group_key: string;
  connection_id: string | null;
  filename: string;
  size: number;
  received: number;
  chunk_size: number;
  status: "receiving" | "processing" | "ready" | "failed";
  error: string | null;
  seconds: number | null;
  duration: string;
  width: number | null;
  height: number | null;
  poster_url: string | null;
  kept: boolean;
}

export interface VideoDraft {
  content_id: string;
  group_key: string;
  connection_id: string;
  shop_name: string | null;
  video_state: "sending" | "attached" | "failed" | null;
  video_error: string | null;
  video_id: string | null;
}

export interface BatchVideos {
  limits: { min_seconds: number; max_seconds: number; max_bytes: number; extensions: string[] };
  videos: ListingVideo[];
  drafts: VideoDraft[];
}

export function secondsText(seconds: number): string {
  const rounded = Math.round(seconds * 10) / 10;
  return Number.isInteger(rounded) ? `${rounded} s` : `${rounded.toFixed(1)} s`;
}

export function megabytes(size: number): string {
  const mb = size / (1024 * 1024);
  return mb >= 10 ? `${mb.toFixed(0)} MB` : `${mb.toFixed(1)} MB`;
}

/** 0:08, as a player shows it. */
export function durationLabel(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "";
  const whole = Math.round(seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

/** What is wrong with a file before any of it is sent: its kind, then its size. */
export function fileProblem(file: { name: string; size: number }): string | null {
  if (!EXTENSIONS.some((ext) => file.name.toLowerCase().endsWith(ext))) return NOT_A_VIDEO;
  if (file.size > MAX_BYTES) return `This video is ${megabytes(file.size)}; Etsy takes videos up to ${megabytes(MAX_BYTES)}.`;
  if (file.size <= 0) return UNREADABLE;
  return null;
}

export function durationProblem(seconds: number | null): string | null {
  if (seconds === null || !(seconds > 0)) return UNREADABLE;
  if (seconds < MIN_SECONDS - SECONDS_TOLERANCE || seconds > MAX_SECONDS + SECONDS_TOLERANCE) {
    return `This video is ${secondsText(seconds)}; Etsy takes ${MIN_SECONDS}–${MAX_SECONDS} s.`;
  }
  return null;
}

/** One line under a video that is there: "0:08 · clip.mp4". */
export function videoLine(video: Pick<ListingVideo, "duration" | "filename">): string {
  return [video.duration, video.filename].filter(Boolean).join(" · ");
}

/** What the slot says of a draft on Etsy (the brief's own words for a video Etsy did not take). */
export function draftLine(draft: Pick<VideoDraft, "video_state" | "video_error" | "shop_name">, several: boolean): string {
  const where = several && draft.shop_name ? ` in ${draft.shop_name}` : "";
  if (draft.video_state === "attached") return `Video attached to the draft${where}.`;
  if (draft.video_state === "sending") return `Sending the video to the draft${where}…`;
  if (draft.video_state === "failed") return `Draft made${where}; the video couldn't be added: ${draft.video_error ?? "Etsy gave no reason."}`;
  return `The draft${where} has no video yet.`;
}

/** The listing's current video (ready), and the newest one still on its way or refused. */
export function slotOf(videos: ListingVideo[], groupKey: string, shopId: string | null): { current: ListingVideo | null; pending: ListingVideo | null } {
  const mine = videos.filter((v) => v.group_key === groupKey && (v.connection_id ?? null) === shopId);
  const ready = mine.filter((v) => v.status === "ready");
  const others = mine.filter((v) => v.status !== "ready");
  return { current: ready[ready.length - 1] ?? null, pending: others[others.length - 1] ?? null };
}

/** Whether the page should look again soon: something is arriving, being read, or being sent. */
export function busy(videos: ListingVideo[], drafts: VideoDraft[], groupKey: string): boolean {
  return videos.some((v) => v.group_key === groupKey && v.status === "processing")
    || drafts.some((d) => d.group_key === groupKey && d.video_state === "sending");
}

/** How long a video is, read by the browser from the file itself (nothing is sent). null: it cannot tell,
 *  and the server reads it after the upload. */
export function readSeconds(file: File, timeoutMs = 8000): Promise<number | null> {
  if (typeof document === "undefined") return Promise.resolve(null);
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const el = document.createElement("video");
    let done = false;
    const finish = (value: number | null) => {
      if (done) return;
      done = true;
      URL.revokeObjectURL(url);
      resolve(value);
    };
    el.preload = "metadata";
    el.muted = true;
    el.onloadedmetadata = () => finish(Number.isFinite(el.duration) && el.duration > 0 ? el.duration : null);
    el.onerror = () => finish(null);
    setTimeout(() => finish(null), timeoutMs);
    el.src = url;
  });
}

/** "Uploading 40%" */
export function uploadingLine(sent: number, total: number): string {
  return `Uploading ${total > 0 ? Math.min(100, Math.floor((sent / total) * 100)) : 0}%`;
}

/** What a failed upload says, with the file's name: "Couldn't upload clip.mp4. Try again." unless the server said why. */
export function failedLine(name: string, error: unknown): string {
  const { status, message } = (error ?? {}) as { status?: number; message?: string };
  if (typeof status === "number" && status >= 400 && status < 500 && status !== 409 && message) return message;
  return `Couldn't upload ${name}. Try again.`;
}
