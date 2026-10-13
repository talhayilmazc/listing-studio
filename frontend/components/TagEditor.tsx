"use client";

import { useEffect, useState } from "react";
import { MAX_TAG_LENGTH as MAX_TAG, TAG_COUNT as REQUIRED, UNDO_SECONDS, addTags, editTag, restoreTags, tagProblems } from "@/lib/tags";

import { Txt } from "@/components/Txt";
export function TagEditor({
  tags,
  onChange,
}: {
  tags: string[];
  onChange: (tags: string[]) => void;
}) {
  const [draft, setDraft] = useState("");
  // "Delete all" clears at once, with no dialog; the tags can be taken back for ten seconds.
  const [deleted, setDeleted] = useState<string[] | null>(null);
  useEffect(() => {
    if (!deleted) return;
    const t = setTimeout(() => setDeleted(null), UNDO_SECONDS * 1000);
    return () => clearTimeout(t);
  }, [deleted]);

  function deleteAll() {
    if (tags.length === 0) return;
    setDeleted(tags);
    setDraft("");
    onChange([]);
  }

  function undo() {
    if (!deleted) return;
    onChange(restoreTags(deleted, tags));
    setDeleted(null);
  }

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
      <div className="mb-1 flex flex-wrap items-center justify-between gap-x-3">
        <span className="label mb-0">Tags</span>
        <span className="flex items-center gap-3">
          {deleted ? (
            <span key="undo" role="status" className="flex items-center gap-2 text-xs text-slate-600">
              <span translate="no">{`${deleted.length} tag${deleted.length === 1 ? "" : "s"} deleted`}</span>
              <button type="button" className="tap font-medium text-brand-700 underline" onClick={undo} data-testid="tags-undo">
                Undo
              </button>
            </span>
          ) : tags.length > 0 ? (
            <button key="delete-all" type="button" className="tap text-xs text-slate-500 underline hover:text-rose-700" onClick={deleteAll} data-testid="tags-delete-all">
              Delete all
            </button>
          ) : null}
          <span className={`text-xs font-medium ${countTone}`} data-testid="tag-count" translate="no">
            <span><span>{tags.length}</span> / <span>{REQUIRED}</span></span>
          </span>
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
