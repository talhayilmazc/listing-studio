"use client";

import { useState } from "react";

const MAX_TAG = 20;
const REQUIRED = 13;

export function TagEditor({
  tags,
  onChange,
}: {
  tags: string[];
  onChange: (tags: string[]) => void;
}) {
  const [draft, setDraft] = useState("");

  function add() {
    const value = draft.trim();
    if (!value) return;
    onChange([...tags, value]);
    setDraft("");
  }

  function remove(i: number) {
    onChange(tags.filter((_, idx) => idx !== i));
  }

  function edit(i: number, value: string) {
    onChange(tags.map((t, idx) => (idx === i ? value : t)));
  }

  const countTone = tags.length === REQUIRED ? "text-emerald-700" : "text-amber-700";

  return (
    <div>
      <div className="mb-1 flex items-center justify-between">
        <span className="label mb-0">Tags</span>
        <span className={`text-xs font-medium ${countTone}`}>
          {tags.length} / {REQUIRED}
        </span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {tags.map((t, i) => {
          const tooLong = t.length > MAX_TAG;
          return (
            <span
              key={i}
              className={`inline-flex items-center gap-1.5 rounded-full border py-1 pl-2.5 pr-1.5 text-xs transition-colors ${
                tooLong
                  ? "border-amber-300 bg-amber-50 text-amber-800"
                  : "border-slate-200 bg-white text-slate-700 hover:border-slate-300"
              }`}
            >
              <input
                value={t}
                onChange={(e) => edit(i, e.target.value)}
                className="bg-transparent focus:outline-none"
                style={{ width: `${Math.max(t.length, 4) + 1}ch` }}
                aria-label={`Tag ${i + 1}`}
              />
              {/* Each chip carries its own length, so the 20-char limit is visible per tag. */}
              <span
                className={`tabular-nums ${tooLong ? "font-medium text-amber-700" : "text-slate-400"}`}
                title={`${t.length} of ${MAX_TAG} characters`}
              >
                {t.length}
              </span>
              <button
                type="button"
                onClick={() => remove(i)}
                className="rounded-full px-0.5 text-slate-400 hover:text-rose-600"
                aria-label={`Remove ${t}`}
              >
                ×
              </button>
            </span>
          );
        })}
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              add();
            }
          }}
          onBlur={add}
          placeholder="add tag…"
          className="min-w-[8ch] flex-1 rounded-full border border-dashed border-slate-300 px-2 py-1 text-xs focus:border-brand-500 focus:outline-none"
        />
      </div>
    </div>
  );
}
