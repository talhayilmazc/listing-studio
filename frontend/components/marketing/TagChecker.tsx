"use client";

import { useMemo, useState } from "react";
import { BrandWarning, RuleList } from "@/components/marketing/ToolParts";
import { MAX_TAG, MAX_TAGS } from "@/lib/etsyText";
import { summary } from "@/lib/freeTools/check";
import { TAG_EXAMPLE, TAG_EXAMPLE_TITLE, checkTags, parseTags, tagBrands } from "@/lib/freeTools/tagCheck";

/** Checks as you type. Nothing leaves the page. */
export function TagChecker() {
  const [text, setText] = useState("");
  const [title, setTitle] = useState("");
  const tags = useMemo(() => parseTags(text), [text]);
  const lines = useMemo(() => checkTags(tags, title), [tags, title]);
  const brands = useMemo(() => tagBrands(tags), [tags]);
  return (
    <div className="space-y-4">
      <div className="border border-heather bg-white p-4 sm:p-5">
        <label htmlFor="tool-tags" className="label">Your tags, separated by commas or one per line</label>
        <textarea id="tool-tags" rows={5} value={text} onChange={(e) => setText(e.target.value)} className="field text-base" data-testid="tags-input" spellCheck={false} />
        <p className="mt-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-1 text-sm text-slate-600">
          <span translate="no" data-testid="tags-count">{`${tags.length} of ${MAX_TAGS} tags`}</span>
          <button type="button" onClick={() => { setText(TAG_EXAMPLE); setTitle(TAG_EXAMPLE_TITLE); }} className="tap mk-link" data-testid="tags-example">Try an example</button>
        </p>
        <label htmlFor="tool-tags-title" className="label mt-4">Your title (optional)</label>
        <input id="tool-tags-title" value={title} onChange={(e) => setTitle(e.target.value)} className="field text-base" data-testid="tags-title" spellCheck={false} />
      </div>
      <p className="min-h-[1.5rem] font-medium text-slate-900" role="status" translate="no" data-testid="tool-summary">{tags.length ? summary(lines) : ""}</p>
      {tags.length > 0 && (
        <ul key="tag-list" className="flex flex-wrap gap-2" translate="no" data-testid="tag-list" aria-label="Your tags and their lengths">
          {tags.map((tag, i) => {
            const size = [...tag].length;
            return (
              <li key={`${i}-${tag}`} className={"max-w-full break-words rounded-lg border px-2.5 py-1 text-sm " + (size > MAX_TAG ? "border-rose-200 bg-rose-50 text-rose-800" : "border-heather bg-white text-slate-800")}>
                <span>{tag}</span>
                <span className="ml-1.5 text-xs">{`${size}/${MAX_TAG}`}</span>
              </li>
            );
          })}
        </ul>
      )}
      <RuleList lines={lines} />
      <BrandWarning brands={brands} />
    </div>
  );
}
