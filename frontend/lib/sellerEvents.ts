/**
 * Reports what went wrong on a page to the server (``POST /api/events``, backend
 * api/events.py): a failed action (method and API path; never the body) or a page error, with
 * the page's path and a short message. No personal data: the server also removes e-mail
 * addresses. At most ``PER_MINUTE`` a minute from this tab; reporting never throws.
 */

export const PER_MINUTE = 20;
const EVENTS_PATH = "/api/events";

export interface SellerEvent {
  kind: "frontend_error" | "action_failed";
  page: string;
  action?: string;
  message?: string;
}

/** A per-minute budget (``now`` injected for tests). */
export function throttle(limit = PER_MINUTE, now: () => number = Date.now) {
  let start = now();
  let used = 0;
  return () => {
    if (now() - start >= 60_000) {
      start = now();
      used = 0;
    }
    return used++ < limit;
  };
}

/** Which failed requests are reported: every failed change, and loads that could not reach
 * the server or that it failed (not "signed out", not "not found"), never the report itself. */
export function worthReporting(method: string, path: string, status: number): boolean {
  if (path.startsWith(EVENTS_PATH)) return false;
  if (status === 401) return false;
  if (method.toUpperCase() !== "GET") return true;
  return status === 0 || status >= 500;
}

/** How long after the browser starts leaving a page a request that fails is its doing, not an outage. */
const LEAVING_MS = 3000;
let leftAt = -Infinity;

export function noteLeaving(now: number = Date.now()): void {
  leftAt = now;
}

/** The browser cuts off what is in flight when a page is left, and that looks like a lost
 * connection: false "could not reach" reports each time a seller moved on before a page finished. */
export function pageIsLeaving(now: number = Date.now()): boolean {
  return now - leftAt < LEAVING_MS;
}

if (typeof window !== "undefined") {
  window.addEventListener("beforeunload", () => noteLeaving());
  window.addEventListener("pagehide", () => noteLeaving());
}

const allowed = throttle();

export function report(event: SellerEvent): void {
  if (typeof window === "undefined" || !allowed()) return;
  try {
    void fetch(EVENTS_PATH, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...event, page: event.page.split("?")[0], message: event.message?.slice(0, 300) }),
      keepalive: true,
    }).catch(() => undefined);
  } catch {
    // reporting must never break the page
  }
}

/** Page errors (uncaught errors and rejected promises) are reported once each. */
export function installErrorReporting(): () => void {
  if (typeof window === "undefined") return () => undefined;
  const onError = (e: ErrorEvent) => report({ kind: "frontend_error", page: location.pathname, message: e.message });
  const onRejection = (e: PromiseRejectionEvent) =>
    report({ kind: "frontend_error", page: location.pathname, message: String((e.reason as Error)?.message ?? e.reason) });
  window.addEventListener("error", onError);
  window.addEventListener("unhandledrejection", onRejection);
  return () => {
    window.removeEventListener("error", onError);
    window.removeEventListener("unhandledrejection", onRejection);
  };
}
