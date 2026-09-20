"use client";

import { usePathname } from "next/navigation";
import { useState } from "react";
import { Rail } from "./Rail";
import { TopStrip } from "./TopStrip";
import { MetricStrip } from "./MetricStrip";
import { Footer } from "./Footer";
import { isPublicRoute, useSession } from "./SessionProvider";

/**
 * Layout frame (docs/ui-direction-v2.md §1): fixed 240px dark rail, content area
 * filling the remaining width. No centred column — the content only stops growing
 * past 1800px.
 *
 * Signed-out screens bypass the frame entirely: they bring their own (AuthShell),
 * because a rail full of links you cannot follow is worse than no rail.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const [navOpen, setNavOpen] = useState(false);
  const { account, loading } = useSession();
  const pathname = usePathname() ?? "/";

  // Auth screens and the forced password change render bare.
  if (isPublicRoute(pathname) || pathname === "/password") {
    return <>{children}</>;
  }

  // Never paint a protected screen before the session is known: the rail and
  // metric strip would fire tenant-scoped requests that 401 on the way out.
  if (loading || !account) {
    return <ShellSkeleton />;
  }

  return (
    <div className="min-h-screen">
      <Rail open={navOpen} onClose={() => setNavOpen(false)} />
      <div className="flex min-h-screen flex-col lg:pl-60">
        <TopStrip onMenu={() => setNavOpen(true)} />
        <MetricStrip />
        <main className="flex-1 px-6 py-8 lg:px-8">
          <div className="mx-auto w-full max-w-[1800px]">{children}</div>
        </main>
        <Footer />
      </div>
    </div>
  );
}

/** The frame's silhouette while the session resolves — skeletons, not a spinner. */
function ShellSkeleton() {
  return (
    <div className="min-h-screen">
      <aside className="fixed inset-y-0 left-0 hidden w-60 bg-[var(--rail)] lg:block" />
      <div className="flex min-h-screen flex-col lg:pl-60">
        <div className="border-b border-slate-200 px-6 py-6 lg:px-8">
          <div className="h-9 w-48 animate-pulse rounded bg-slate-100" />
        </div>
        <div className="border-b border-slate-200 px-6 py-5 lg:px-8">
          <div className="h-8 w-full max-w-md animate-pulse rounded bg-slate-100" />
        </div>
        <main className="flex-1 px-6 py-8 lg:px-8">
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {[0, 1, 2].map((i) => (
              <div key={i} className="card overflow-hidden">
                <div className="aspect-[16/10] w-full animate-pulse bg-slate-100" />
                <div className="space-y-2 p-5">
                  <div className="h-5 w-32 animate-pulse rounded bg-slate-100" />
                  <div className="h-3 w-40 animate-pulse rounded bg-slate-100" />
                </div>
              </div>
            ))}
          </div>
        </main>
      </div>
    </div>
  );
}
