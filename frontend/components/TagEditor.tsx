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

  const countTone =
    tags.length === REQUIRED ? "text-emerald-600" : "text-amber-600";

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
              className={`inline-flex items-center gap-1 rounded-full border px-2 py-1 text-xs ${
                tooLong
                  ? "border-rose-300 bg-rose-50 text-rose-700"
                  : "border-slate-200 bg-slate-50 text-slate-700"
              }`}
            >
              <input
                value={t}
                onChange={(e) => edit(i, e.target.value)}
                className="w-[7.5ch] bg-transparent focus:outline-none"
                style={{ width: `${Math.max(t.length, 4) + 1}ch` }}
              />
              <button
                type="button"
                onClick={() => remove(i)}
                className="text-slate-400 hover:text-rose-500"
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
