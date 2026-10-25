"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { PersonalizationField, PersonalizationHold } from "@/lib/types";
import { counter } from "@/lib/personalization";
import { Txt } from "@/components/Txt";

/**
 * The one-click fix for personalization text Etsy would refuse (backend api/personalization_fix.py):
 * on a profile and on any listing card held for this reason. Per field, the seller's text with its
 * counter, and beside it an offer: the caps fixed (only the first word in capitals kept), or a
 * shorter version within Etsy's limit written by the content model. Nothing changes until "Use this".
 */
export function PersonalizationFix({ hold, onUse, scope }: {
  hold: PersonalizationHold;
  /** Saves the new text (the profile's, or the listing's own); resolves with a line to show. */
  onUse: (body: { question_text?: string; instructions?: string }) => Promise<string | null>;
  /** What "Use this" changes, in a few words: "the profile “Classic tees”" or "this listing". */
  scope: string;
}) {
  const [done, setDone] = useState<string | null>(null);
  return (
    <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-xs text-amber-950" role="status" data-testid="personalization-hold">
      <p className="font-medium"><Txt>{hold.reason}</Txt></p>
      {hold.fields.map((f) => (
        <FieldFix key={f.field} field={f} scope={scope}
          onUse={async (value) => {
            const line = await onUse(f.field === "question" ? { question_text: value } : { instructions: value });
            setDone(line ?? "Saved.");
          }} />
      ))}
      {done && <p key="done" className="mt-2 text-emerald-800" translate="no" data-testid="personalization-fix-done">{done}</p>}
    </div>
  );
}

function FieldFix({ field, scope, onUse }: {
  field: PersonalizationField;
  scope: string;
  onUse: (value: string) => Promise<void>;
}) {
  const [offer, setOffer] = useState<string | null>(field.needs_shortening ? null : field.caps_fix);
  const [asking, setAsking] = useState(field.needs_shortening);
  const [message, setMessage] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const label = field.field === "question" ? "Question" : "Instructions";

  // Too long: the shorter version is asked for at once (one model call per text, kept a week).
  useEffect(() => {
    if (!field.needs_shortening) return;
    let gone = false;
    setAsking(true);
    api.shortenPersonalization(field.field, field.caps_fix ?? field.text)
      .then((r) => {
        if (gone) return;
        setOffer(r.text);
        setMessage(r.message);
      })
      .catch((e: Error) => !gone && setMessage(e.message))
      .finally(() => !gone && setAsking(false));
    return () => {
      gone = true;
    };
  }, [field.field, field.text, field.caps_fix, field.needs_shortening]);

  const now = counter(field.text, field.limit);
  const next = offer ? counter(offer, field.limit) : null;
  return (
    <div className="mt-2 grid gap-2 sm:grid-cols-2" data-testid={`personalization-fix-${field.field}`}>
      <div className="min-w-0 rounded border border-amber-200 bg-white p-2">
        <p className="label"><span>{`${label} now`}</span></p>
        <p className="mt-0.5 break-words text-slate-800"><Txt>{field.text}</Txt></p>
        <p className={`mt-1 ${now.over ? "text-rose-700" : "text-slate-600"}`} translate="no">
          <span>{now.line}</span>
          <span>{field.extra_caps > 0 ? ` · ${field.extra_caps + 1} words in capitals (Etsy allows one)` : ""}</span>
        </p>
      </div>
      <div className="min-w-0 rounded border border-emerald-200 bg-white p-2">
        <p className="label">{field.needs_shortening ? "Shorter, same meaning" : "Only the first word in capitals"}</p>
        {asking ? (
          <p key="asking" className="mt-0.5 text-slate-600" aria-busy="true">Writing a shorter version…</p>
        ) : offer ? (
          <p key="offer" className="mt-0.5 break-words text-slate-800" data-testid="personalization-offer"><Txt>{offer}</Txt></p>
        ) : (
          <p key="none" className="mt-0.5 text-slate-600"><Txt>{message ?? "No suggestion: edit the text yourself."}</Txt></p>
        )}
        {next && <p key="count" className="mt-1 text-slate-600" translate="no">{next.line}</p>}
        {offer && (
          <button key="use" type="button" className="btn-primary mt-2 px-2.5 py-1 text-xs" disabled={saving}
            onClick={async () => {
              setSaving(true);
              setMessage(null);
              try {
                await onUse(offer);
              } catch (e) {
                setMessage((e as Error).message);
              } finally {
                setSaving(false);
              }
            }} data-testid="personalization-use">
            {saving ? "Saving…" : "Use this"}
          </button>
        )}
        {offer && message && <p key="error" role="alert" className="mt-1 text-rose-700"><Txt>{message}</Txt></p>}
        {offer && <p key="scope" className="mt-1 text-[11px] text-slate-600"><span>Changes </span><Txt>{scope}</Txt><span>; held listings then go ahead by themselves.</span></p>}
      </div>
    </div>
  );
}
