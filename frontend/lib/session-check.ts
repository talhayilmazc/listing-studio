/**
 * Is a session cookie a live session? Asked by middleware.ts for "/", so a
 * signed-in visitor goes to their dashboard and everyone else sees the site.
 *
 * Three answers, because "could not tell" is not "invalid": an API that is
 * down or slow must not sign anyone out, it only means the landing page shows.
 */

export type SessionVerdict = "valid" | "invalid" | "unknown";

export const SESSION_COOKIE = "session";
/** Long enough for a healthy API on the same network, short enough not to hold the page. */
export const CHECK_TIMEOUT_MS = 1500;

type Fetch = (url: string, init: RequestInit) => Promise<Response>;

export async function checkSession(
  apiBase: string,
  token: string,
  forward: Record<string, string> = {},
  fetcher: Fetch = fetch,
  timeoutMs: number = CHECK_TIMEOUT_MS,
): Promise<SessionVerdict> {
  if (!token) return "invalid";
  try {
    const res = await fetcher(`${apiBase.replace(/\/$/, "")}/api/account/session`, {
      method: "GET",
      headers: { cookie: `${SESSION_COOKIE}=${token}`, ...forward },
      cache: "no-store",
      redirect: "manual",
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (res.status === 204 || res.status === 200) return "valid";
    if (res.status === 401) return "invalid";
    return "unknown"; // 429, 5xx: the API could not say
  } catch {
    return "unknown"; // unreachable or too slow
  }
}
