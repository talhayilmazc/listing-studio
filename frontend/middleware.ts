import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE, checkSession } from "./lib/session-check";

/**
 * "/" is the public site. A visitor with a LIVE session goes straight to their
 * dashboard instead. The cookie's presence is not enough: an expired or
 * signed-out cookie stays in the browser, and it used to send the visitor to
 * /dashboard and on to /login, so they never saw the site. So the API is
 * asked (one session lookup, on the internal network, never through the
 * public edge), and:
 *
 *   live session         -> redirect to /dashboard
 *   no session behind it -> the landing page, and the dead cookie is cleared
 *   API did not answer   -> the landing page; the cookie is left alone
 *
 * None of these responses may be cached: they depend on who is asking.
 */

// Same as next.config.js: the API on the internal network.
const API = process.env.API_PROXY_TARGET || "http://localhost:8000";

export async function middleware(request: NextRequest) {
  const token = request.cookies.get(SESSION_COOKIE)?.value;
  if (!token) return NextResponse.next();

  // The API rate-limits and logs by the visitor's address, not this server's.
  const forward: Record<string, string> = {};
  for (const name of ["cf-connecting-ip", "x-forwarded-for", "user-agent"]) {
    const value = request.headers.get(name);
    if (value) forward[name] = value;
  }

  const verdict = await checkSession(API, token, forward);
  if (verdict === "valid") {
    const to = NextResponse.redirect(new URL("/dashboard", request.url));
    to.headers.set("Cache-Control", "private, no-store");
    return to;
  }
  const page = NextResponse.next();
  page.headers.set("Cache-Control", "private, no-store");
  if (verdict === "invalid") {
    // Path and name as the API set it (host-only, path "/").
    page.cookies.set({ name: SESSION_COOKIE, value: "", path: "/", maxAge: 0, httpOnly: true, sameSite: "lax" });
  }
  return page;
}

export const config = { matcher: "/" };
