"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { tabsFor } from "@/lib/sectionTabs";

/** The pages of one rail entry, as tabs under the title (lib/sectionTabs.ts). */
export function SectionTabs() {
  const found = tabsFor(usePathname() ?? "/");
  if (!found) return null;
  return (
    <nav aria-label="In this section" className="border-b border-slate-200 px-6 lg:px-8">
      <ul className="-mb-px flex gap-1 overflow-x-auto">
        {found.tabs.map((t) => {
          const current = t.href === found.current;
          return (
            <li key={t.href} className="shrink-0">
              <Link
                href={t.href}
                aria-current={current ? "page" : undefined}
                className={
                  "flex min-h-[2.75rem] items-center border-b-2 px-3 text-sm " +
                  (current ? "border-ink font-semibold text-slate-900" : "border-transparent text-slate-600 hover:text-slate-900")
                }
              >
                {t.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
