"use client";

import { useMemo, useState } from "react";
import { BrandWarning, RuleList } from "@/components/marketing/ToolParts";
import { summary } from "@/lib/freeTools/check";
import { TITLE_EXAMPLE, checkTitle, titleBrands } from "@/lib/freeTools/titleCheck";
import { MAX_TITLE } from "@/lib/etsyText";

/** Checks as you type. Nothing leaves the page. */
export function TitleChecker() {
  const [title, setTitle] = useState("");
  const lines = useMemo(() => checkTitle(title), [title]);
  const brands = useMemo(() => titleBrands(title), [title]);
  const empty = !title.trim();
  return (
    <div className="space-y-4">
      <div className="border border-heather bg-white p-4 sm:p-5">
        <label htmlFor="tool-title" className="label">Your title</label>
        <textarea id="tool-title" rows={3} value={title} onChange={(e) => setTitle(e.target.value)} className="field text-base" data-testid="title-input" spellCheck={false} />
        <p className="mt-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-1 text-sm text-slate-600">
          <span translate="no" data-testid="title-count">{`${[...title].length} of ${MAX_TITLE} characters`}</span>
          <button type="button" onClick={() => setTitle(TITLE_EXAMPLE)} className="tap mk-link" data-testid="title-example">Try an example</button>
        </p>
      </div>
      <p className="min-h-[1.5rem] font-medium text-slate-900" role="status" translate="no" data-testid="tool-summary">{empty ? "" : summary(lines)}</p>
      <RuleList lines={lines} />
      <BrandWarning brands={brands} />
    </div>
  );
}
