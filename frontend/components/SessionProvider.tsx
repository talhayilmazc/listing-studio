"use client";

import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { Account } from "@/lib/types";

/**
 * Who is signed in, and the route guard that depends on it (production-spec B1).
 *
 * The API is the only authority: the session lives in an HttpOnly cookie the
 * page cannot read, so the app asks `/account/me` and believes the answer. A 401
 * sends the browser to /login; nothing renders in between, so a protected screen
 * never paints for a signed-out visitor.
 */

/** Routes that render without a session. */
const PUBLIC_ROUTES = ["/login", "/register", "/terms", "/privacy"];

/** Reachable while the account still carries an admin-issued temporary password. */
const TEMP_PASSWORD_ROUTES = [...PUBLIC_ROUTES, "/password"];

export function isPublicRoute(pathname: string): boolean {
  return PUBLIC_ROUTES.includes(pathname);
}

interface SessionValue {
  account: Account | null;
  /** True until the first /account/me answer lands. */
  loading: boolean;
  setAccount: (account: Account | null) => void;
  refresh: () => Promise<void>;
  signOut: () => Promise<void>;
}

const SessionContext = createContext<SessionValue>({
  account: null,
  loading: true,
  setAccount: () => {},
  refresh: async () => {},
  signOut: async () => {},
});

export const useSession = () => useContext(SessionContext);

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [account, setAccount] = useState<Account | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();
  const pathname = usePathname() ?? "/";

  const refresh = useCallback(async () => {
    try {
      setAccount(await api.me());
    } catch (e) {
      // 401 is the ordinary signed-out answer, not an error worth surfacing.
      if (!(e instanceof ApiError) || e.status !== 401) console.error(e);
      setAccount(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const signOut = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      setAccount(null);
      router.replace("/login");
    }
  }, [router]);

  // Route guard. Runs after every answer, so a session that expires mid-visit
  // bounces on the next check rather than leaving a dead screen.
  useEffect(() => {
    if (loading) return;
    const isPublic = PUBLIC_ROUTES.includes(pathname);

    if (!account && !isPublic) {
      router.replace("/login");
      return;
    }
    if (account && (pathname === "/login" || pathname === "/register")) {
      router.replace("/dashboard");
      return;
    }
    // A temporary password unlocks nothing but the change-password screen,
    // mirroring the server's own rule (production-spec A4).
    if (account?.must_change_password && !TEMP_PASSWORD_ROUTES.includes(pathname)) {
      router.replace("/password");
    }
  }, [account, loading, pathname, router]);

  return (
    <SessionContext.Provider value={{ account, loading, setAccount, refresh, signOut }}>
      {children}
    </SessionContext.Provider>
  );
}
