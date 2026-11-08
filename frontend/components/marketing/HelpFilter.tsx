"use client";

import { useEffect, useState } from "react";

/**
 * The help center's search box. Every answer is in the page's HTML (search engines and readers without
 * script get them all); this only hides the ones that do not match what is typed. Nothing is sent anywhere.
 */
export function HelpFilter() {
  const [query, setQuery] = useState("");
  const [shown, setShown] = useState<number | null>(null);

  useEffect(() => {
    const words = query.toLowerCase().split(/\s+/).filter(Boolean);
    let count = 0;
    for (const group of document.querySelectorAll<HTMLElement>("[data-help-group]")) {
      let inGroup = 0;
      for (const item of group.querySelectorAll<HTMLElement>("[data-help-item]")) {
        const text = (item.textContent ?? "").toLowerCase();
        const match = words.every((word) => text.includes(word));
        item.hidden = !match;
        if (match) inGroup += 1;
      }
      group.hidden = inGroup === 0;
      count += inGroup;
    }
    setShown(words.length ? count : null);
  }, [query]);

  return (
    <div className="mb-10" role="search">
      <label htmlFor="help-search" className="block text-[0.9375rem] font-bold text-ink">Search the answers</label>
      <input
        id="help-search"
        type="search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="For example: schedule, trademark, delete"
        className="mt-2 block min-h-[3rem] w-full max-w-[32rem] rounded-[4px] border-[1.5px] border-ink bg-white px-3 text-base text-ink placeholder:text-slate-500"
        data-testid="help-search"
      />
      <p className="mt-2 min-h-[1.5rem] text-[0.9375rem] text-slate-600" role="status" translate="no" data-testid="help-count">
        {shown === null ? "" : shown === 0 ? "No answer matches. Try another word, or use the contact page." : `${shown} ${shown === 1 ? "answer matches" : "answers match"}`}
      </p>
    </div>
  );
}
