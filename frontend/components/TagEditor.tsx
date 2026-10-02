"use client";

import { useState } from "react";
import { MAX_TAG_LENGTH as MAX_TAG, TAG_COUNT as REQUIRED, addTags, editTag, tagProblems } from "@/lib/tags";

import { Txt } from "@/components/Txt";
export function TagEditor({
  tags,
  onChange,
}: {
  tags: string[];
  onChange: (tags: string[]) => void;
}) {
  const [draft, setDraft] = useState("");

  // "nurse, winter, sweatshirt" is three tags, typed or pasted (v7 §E4).
  function add(text = draft) {
    if (!text.trim()) return;
    onChange(addTags(tags, text));
    setDraft("");
  }

  function remove(i: number) {
    onChange(tags.filter((_, idx) => idx !== i));
  }

  function edit(i: number, value: string) {
    onChange(editTag(tags, i, value));
  }

  const problems = tagProblems(tags);

  const countTone = tags.length === REQUIRED ? "text-emerald-700" : "text-amber-700";

  return (
    <div>
      <div className="mb-1 flex items-center justify-between">
        <span className="label mb-0">Tags</span>
        <span className={`text-xs font-medium ${countTone}`}>
          <span><span>{tags.length}</span> / <span>{REQUIRED}</span></span>
        </span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {tags.map((t, i) => {
          const problem = problems[i];
          const tooLong = problem !== null;
          return (
            <span
              key={i}
              className={`inline-flex items-center gap-1.5 rounded-full border py-1 pl-2.5 pr-1.5 text-xs transition-colors max-sm:py-0 max-sm:pl-3 ${
                tooLong
                  ? "border-amber-300 bg-amber-50 text-amber-800"
                  : "border-slate-200 bg-white text-slate-700 hover:border-slate-300"
              }`}
            >
              <input
                value={t}
                onChange={(e) => edit(i, e.target.value)}
                className="bg-transparent focus:outline-none max-sm:h-10 max-sm:text-base"
                style={{ width: `${Math.max(t.length, 4) + 1}ch` }}
                aria-label={`Tag ${i + 1}`}
              />
              {/* Each chip carries its own length, so the 20-char limit is visible per tag. */}
              <span translate="no"
                className={`tabular-nums ${t.length > MAX_TAG ? "font-medium text-amber-700" : "text-slate-400"}`}
                title={problem ? `${t.length} of ${MAX_TAG} characters: ${problem}` : `${t.length} of ${MAX_TAG} characters`}
              >
                {t.length}
              </span>
              <button
                type="button"
                onClick={() => remove(i)}
                className="rounded-full px-0.5 text-slate-400 hover:text-rose-600 max-sm:-mr-1 max-sm:flex max-sm:h-10 max-sm:w-10 max-sm:items-center max-sm:justify-center max-sm:text-lg"
                aria-label={`Remove ${t}`}
              >
                ×
              </button>
            </span>
          );
        })}
        <input
          value={draft}
          onChange={(e) => {
            // A pasted or typed list becomes tags at once; the last piece keeps typing.
            const v = e.target.value;
            if (/[,;\n]/.test(v)) {
              const parts = v.split(/[,;\n]/);
              const rest = parts.pop() ?? "";
              onChange(addTags(tags, parts.join(",")));
              setDraft(rest.trimStart());
            } else {
              setDraft(v);
            }
          }}
          onPaste={(e) => {
            const text = e.clipboardData.getData("text");
            if (/[,;\n]/.test(text)) {
              e.preventDefault();
              add(draft + text);
            }
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              add();
            }
          }}
          onBlur={() => add()}
          placeholder="add tags, separated by commas…"
          className="min-w-[8ch] flex-1 rounded-full border border-dashed border-slate-300 px-2 py-1 text-xs focus:border-brand-500 focus:outline-none max-sm:min-h-[2.75rem] max-sm:basis-full max-sm:px-3 max-sm:text-base"
        />
      </div>
      {problems.some(Boolean) && (
        <p key="p-112-6" className="mt-1 text-xs text-amber-700">
          <span><Txt>{problems.filter((p) => p === "duplicate").length > 0 && "Duplicate tags are counted once by Etsy. "}</Txt>
          <Txt>{problems.some((p) => p?.startsWith("over")) && `Tags over ${MAX_TAG} characters are refused.`}</Txt></span>
        </p>
      )}
    </div>
  );
}
