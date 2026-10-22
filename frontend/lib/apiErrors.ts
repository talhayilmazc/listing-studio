/**
 * What a failed request says to the seller (quality pass 2026-10): always plain words with what
 * to do, never a raw JSON list. The server's own sentence is kept as it is; an object detail
 * stays JSON, because some screens read its fields (the review card's approval errors).
 */

const SUPPORT = "if it keeps happening, write to us with the reference";

/** FastAPI's validation list, as one sentence: "Check the form: email: value is not a valid email address". */
function fieldErrors(detail: unknown[]): string | null {
  const parts = detail
    .map((d) => {
      if (!d || typeof d !== "object" || !("msg" in d)) return null;
      const item = d as { loc?: unknown; msg: unknown };
      const loc = Array.isArray(item.loc) ? item.loc : [];
      const field = loc.filter((p) => p !== "body" && p !== "query" && p !== "path").join(".");
      const msg = String(item.msg).replace(/^Value error, /, "");
      return field ? `${field}: ${msg}` : msg;
    })
    .filter(Boolean);
  return parts.length ? `Check what was sent: ${Array.from(new Set(parts)).join("; ")}.` : null;
}

export function messageFor(status: number, body: unknown, statusText = ""): string {
  const detail = body && typeof body === "object" && "detail" in body ? (body as { detail: unknown }).detail : undefined;
  const ref = body && typeof body === "object" && "request_id" in body ? String((body as { request_id: unknown }).request_id) : "";
  if (status >= 500) {
    if (typeof detail === "string" && detail !== "internal error") return detail;
    return `Listyro could not finish this just now. Try again in a minute; ${SUPPORT}${ref ? ` ${ref}` : ""}.`;
  }
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail)) return fieldErrors(detail) ?? "Check what was sent and try again.";
  if (detail && typeof detail === "object") return JSON.stringify(detail);
  if (status === 401) return "You are signed out. Sign in again to carry on.";
  if (status === 404) return "This is no longer here. Reload the page.";
  if (status === 413) return "This file is too large.";
  if (status === 429) return "Too many requests at once. Wait a minute, then try again.";
  return statusText || "This did not work. Try again.";
}

/** A request that never got an answer (offline, the server restarting). */
export const UNREACHABLE = "Could not reach Listyro. Check your connection and try again.";
