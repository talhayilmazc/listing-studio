"use client";

import { useState } from "react";
import { Rail } from "./Rail";
import { TopStrip } from "./TopStrip";
import { MetricStrip } from "./MetricStrip";
import { Footer } from "./Footer";

/**
 * Layout frame (docs/ui-direction-v2.md §1): fixed 240px dark rail, content area
 * filling the remaining width. No centred column — the content only stops growing
 * past 1800px.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const [navOpen, setNavOpen] = useState(false);

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
