import { NextResponse, type NextRequest } from "next/server";

/**
 * "/" is the public site. Someone who is signed in goes straight to their
 * dashboard instead: the session cookie is HttpOnly, but this runs on the
 * server and only needs to know it is there. A cookie whose session has ended
 * lands on /dashboard, where the app's own guard sends it to /login.
 */
export function middleware(request: NextRequest) {
  if (request.cookies.has(SESSION_COOKIE)) {
    return NextResponse.redirect(new URL("/dashboard", request.url));
  }
  return NextResponse.next();
}

const SESSION_COOKIE = "session";

export const config = { matcher: "/" };
