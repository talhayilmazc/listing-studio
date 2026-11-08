"use client";

import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { BATCH_RENAMED } from "./BatchName";
import { useSession } from "./SessionProvider";

/**
 * Top strip: the page's title, the same word as its entry in the rail (app/onboarding-v1).
 * No buttons: each page has its own one primary action, and "Upload designs" is on Designs and
 * Home, not in every page's header (it was the second upload button on every screen).
 */

export const TITLES: [RegExp, string][] = [
  [/^\/dashboard$/, "Home"],
  [/^\/batches$/, "Designs"],
  [/^\/batches\/[^/]+\/review$/, "Review"],
  [/^\/review$/, "Review"],
  [/^\/profiles$/, "Profiles"],
  [/^\/templates$/, "Mockup templates"],
  [/^\/templates\/designs$/, "Make mockups"],
  [/^\/upload$/, "Upload designs"],
  [/^\/scheduled$/, "Schedule"],
  [/^\/season-planner$/, "Season planner"],
  [/^\/analytics$/, "Reports"],
  [/^\/analytics\/\d+$/, "Listing report"],
  [/^\/listing-health$/, "Listing health"],
  [/^\/connect$/, "Shops"],
  [/^\/settings$/, "Settings"],
  [/^\/admin$/, "Admin"],
  [/^\/terms$/, "Terms of Service"],
  [/^\/privacy$/, "Privacy Policy"],
];

function titleFor(pathname: string, isAdmin: boolean): string {
  // Non-admins get the same generic 404 as any unknown route: no title that says /admin exists.
  if (pathname === "/admin" && !isAdmin) return "Listyro";
  for (const [re, title] of TITLES) if (re.test(pathname)) return title;
  // Batch detail keeps its short id, which the page no longer repeats.
  const batch = pathname.match(/^\/batches\/([^/]+)$/);
  if (batch) return "Batch " + batch[1].slice(0, 8);
  return "Listyro";
}

export function TopStrip({ onMenu }: { onMenu: () => void }) {
  const pathname = usePathname() ?? "/";
  const { account } = useSession();
  // On a batch's page the title is the batch's name, and follows a rename.
  const batchId = pathname.match(/^\/batches\/([^/]+)$/)?.[1] ?? null;
  const [batchName, setBatchName] = useState<string | null>(null);
  useEffect(() => {
    setBatchName(null);
    if (!batchId) return;
    let cancelled = false;
    api.batchSummary(batchId).then((b) => !cancelled && setBatchName(b.name)).catch(() => {});
    const renamed = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (detail?.id === batchId) setBatchName(detail.name);
    };
    window.addEventListener(BATCH_RENAMED, renamed);
    return () => {
      cancelled = true;
      window.removeEventListener(BATCH_RENAMED, renamed);
    };
  }, [batchId]);

  return (
    <header className="border-b border-slate-200">
      <div className="flex items-center gap-4 px-6 py-6 lg:px-8">
        <button
          type="button"
          onClick={onMenu}
          aria-label="Open navigation"
          className="-ml-2 flex h-11 w-11 shrink-0 items-center justify-center rounded-md text-slate-500 hover:bg-slate-100 hover:text-slate-900 lg:hidden"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M3 6h18M3 12h18M3 18h18" strokeLinecap="round" />
          </svg>
        </button>

        <h1 className="min-w-0 flex-1 truncate font-display text-[1.75rem] font-bold leading-tight text-slate-900 sm:text-4xl">
          {batchId && batchName ? batchName : titleFor(pathname, Boolean(account?.is_admin))}
        </h1>

      </div>
    </header>
  );
}
