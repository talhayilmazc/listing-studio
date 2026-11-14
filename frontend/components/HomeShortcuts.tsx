"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { SHORTCUTS, mockupsCard, type Shortcut } from "@/lib/homeShortcuts";

/** Home: mockups first (make them, or add the first template), then the features used weekly. */
export function HomeShortcuts() {
  const [templates, setTemplates] = useState<number | null>(null);
  useEffect(() => {
    api.mockupTemplates().then((t) => setTemplates(t.length)).catch(() => setTemplates(null));
  }, []);
  const mockups = mockupsCard(templates);
  return (
    <section aria-label="Shortcuts" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" data-testid="home-shortcuts">
      {/* A fixed slot: the card arriving never moves the others. */}
      <div key="mockups" className="min-h-[7.5rem]">
        {mockups ? <Card key="card" item={mockups} primary /> : <div key="wait" className="card h-full animate-pulse bg-slate-50" />}
      </div>
      {SHORTCUTS.map((s) => <Card key={s.href} item={s} />)}
    </section>
  );
}

function Card({ item, primary = false }: { item: Shortcut; primary?: boolean }) {
  return (
    <Link href={item.href} data-testid={item.testid}
      className={`card block h-full p-4 transition hover:border-brand-300 focus-visible:outline ${primary ? "border-brand-200 bg-brand-50" : ""}`}>
      <span className="block text-sm font-bold text-slate-900">{item.title}</span>
      <span className="mt-1 block text-sm text-slate-600">{item.line}</span>
    </Link>
  );
}
