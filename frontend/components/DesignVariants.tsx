"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { MockupDesign, MockupPlan } from "@/lib/types";
import { MEANING_LABEL, fileLines, fillLabel, missingLine, type Meaning } from "@/lib/variants";
import { Txt } from "./Txt";

/**
 * Light and dark variants on the upload page (backend pipeline/variants.py): what a suffix such
 * as "-white" names (remembered for the account), and, before any mockup is made, which file
 * goes on which photos of the chosen templates, the photos that get no mockup and why, with a
 * one-click "use it on the other group too". The server's plan is the rule; this only says it.
 */
export function DesignVariants({
  plan,
  designs,
  disabled,
  onMeaning,
  onDesign,
}: {
  plan: MockupPlan | null;
  designs: MockupDesign[];
  disabled?: boolean;
  /** The setting changed: the page asks for the plan again. */
  onMeaning: (m: Meaning) => void;
  onDesign: (d: MockupDesign) => void;
}) {
  const [meaning, setMeaning] = useState<Meaning>(plan?.variant_suffix ?? "shirt");
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (plan) setMeaning(plan.variant_suffix);
  }, [plan]);

  const withVariants = (plan?.designs ?? []).filter((d) => d.variants);
  if (!plan || withVariants.length === 0) return null;

  async function change(m: Meaning) {
    setMeaning(m);
    setError(null);
    try {
      await api.setVariantSetting(m);
      onMeaning(m);
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }

  async function fill(id: string) {
    setError(null);
    try {
      onDesign(await api.setDesignFillOther(id, true));
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }

  return (
    <div className="space-y-3 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm" data-testid="design-variants">
      <fieldset className="space-y-1" disabled={disabled}>
        <legend className="text-xs font-medium text-slate-700">What does a suffix like -white or -dark name?</legend>
        {(Object.keys(MEANING_LABEL) as Meaning[]).map((m) => (
          <label key={m} className="flex min-h-[2.75rem] items-center gap-2 text-sm text-slate-700 sm:min-h-[2rem]">
            <input type="radio" name="variant-meaning" checked={meaning === m} onChange={() => change(m)} data-testid={`variant-meaning-${m}`} />
            <span>{MEANING_LABEL[m]}</span>
          </label>
        ))}
      </fieldset>
      <ul className="space-y-2" data-testid="variant-plan">
        {withVariants.map((d) => {
          const design = designs.find((x) => x.id === d.design_id);
          const missing = missingLine(d, plan.photos, meaning);
          const action = fillLabel(d);
          return (
            <li key={d.design_id} className="rounded border border-slate-200 bg-white p-2.5" data-testid="variant-design">
              <p className="font-medium text-slate-900" translate="no">{design?.parsed_sku ?? design?.group_key ?? "Design"}</p>
              <ul className="mt-1 space-y-0.5 text-xs text-slate-700">
                {fileLines(d, plan.photos).map((line) => <li key={line} translate="no">{line}</li>)}
              </ul>
              {missing && (
                <p key="missing" className="mt-1.5 text-xs text-amber-900" data-testid="variant-missing"><Txt>{missing}</Txt></p>
              )}
              {action && (
                <button key="fill" type="button" className="btn-secondary mt-1.5 px-3 py-1.5 text-xs" disabled={disabled}
                  onClick={() => fill(d.design_id)} data-testid="variant-fill">
                  <span translate="no">{action}</span>
                </button>
              )}
              {d.uncertain.length > 0 && (
                <p key="uncertain" className="mt-1.5 text-xs text-slate-700">
                  <span translate="no">{`${d.uncertain.length} photo${d.uncertain.length === 1 ? " is" : "s are"} close to the line between light and dark; `}</span>
                  <span>check its colour on the template.</span>
                </p>
              )}
            </li>
          );
        })}
      </ul>
      {error && <p key="error" className="text-xs text-rose-700">{error}</p>}
    </div>
  );
}
