"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { AdminUser, AllowanceDefault } from "@/lib/types";
import { periodWord } from "@/components/Allowance";
import { Meter } from "./Usage";

const PERIODS: AllowanceDefault["period"][] = ["daily", "weekly", "monthly"];

/**
 * One seller's product allowance in the users table: this period's use, and an
 * editor for the amount and period (or back to the system default). A change
 * applies at once; what was used this period still counts.
 */
export function AllowanceCell({
  user,
  onSave,
}: {
  user: AdminUser;
  onSave: (amount: number | null, period: string | null) => Promise<boolean>;
}) {
  const a = user.allowance;
  const [editing, setEditing] = useState(false);
  const [amount, setAmount] = useState("");
  const [period, setPeriod] = useState<string>("monthly");
  const [saving, setSaving] = useState(false);
  if (!a) return null;

  const n = Number(amount);
  const valid = amount.trim() !== "" && Number.isInteger(n) && n >= 0 && n <= 1_000_000;

  async function save(nextAmount: number | null, nextPeriod: string | null) {
    setSaving(true);
    const ok = await onSave(nextAmount, nextPeriod);
    setSaving(false);
    if (ok) setEditing(false);
  }

  if (editing) {
    return (
      <form
        className="flex flex-wrap items-center gap-1.5"
        onSubmit={(e) => {
          e.preventDefault();
          if (valid) save(n, period);
        }}
      >
        <input
          autoFocus
          inputMode="numeric"
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          onKeyDown={(e) => e.key === "Escape" && setEditing(false)}
          aria-label={`Allowance for ${user.email}`}
          aria-invalid={!valid}
          className="field w-20 py-1 text-sm tabular-nums"
        />
        <select
          value={period}
          onChange={(e) => setPeriod(e.target.value)}
          aria-label={`Allowance period for ${user.email}`}
          className="field w-auto py-1 text-xs"
        >
          {PERIODS.map((p) => (
            <option key={p} value={p}>
              {`per ${p === "daily" ? "day" : p === "weekly" ? "week" : "month"}`}
            </option>
          ))}
        </select>
        <button type="submit" disabled={!valid || saving} className="btn-primary px-2 py-1 text-xs">
          {saving ? "…" : "Save"}
        </button>
        {a.custom && (
          <button key="default" type="button" disabled={saving} onClick={() => save(null, null)} className="px-1 text-xs text-slate-500 underline hover:text-slate-800">
            Use default
          </button>
        )}
        <button type="button" onClick={() => setEditing(false)} className="px-1 text-xs text-slate-500 hover:text-slate-800">
          Cancel
        </button>
      </form>
    );
  }

  return (
    <div className="min-w-[190px]">
      <div className="flex items-center gap-2.5">
        <div className="w-20">
          <Meter used={a.used} limit={a.amount} label={`${user.email} allowance used`} />
        </div>
        <button
          translate="no"
          type="button"
          onClick={() => {
            setAmount(String(a.amount));
            setPeriod(a.period);
            setEditing(true);
          }}
          title="Change this seller's allowance"
          className="tap rounded px-1 text-xs tabular-nums text-slate-600 underline decoration-slate-300 decoration-dotted underline-offset-2 max-sm:py-2 hover:text-slate-900"
        >
          {`${a.used.toLocaleString()} / ${a.amount.toLocaleString()}`}
        </button>
      </div>
      <p translate="no" className="mt-0.5 whitespace-nowrap text-[11px] text-slate-400" title={`Resets ${a.resets_label}`}>
        {`${a.period}${a.custom ? "" : " (default)"} · resets ${a.resets_label.split(", ").slice(1, 2).join("")}`}
      </p>
    </div>
  );
}

/** The allowance of every seller without their own. Applies at once; audited. */
export function DefaultAllowance({ onSaved, onError }: { onSaved: () => void; onError: (m: string) => void }) {
  const [current, setCurrent] = useState<AllowanceDefault | null>(null);
  const [amount, setAmount] = useState("");
  const [period, setPeriod] = useState<AllowanceDefault["period"]>("monthly");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.admin
      .allowanceDefault()
      .then((d) => {
        setCurrent(d);
        setAmount(String(d.amount));
        setPeriod(d.period);
      })
      .catch((e) => onError(String(e.message ?? e)));
  }, [onError]);

  if (!current) return null;
  const n = Number(amount);
  const valid = amount.trim() !== "" && Number.isInteger(n) && n >= 0 && n <= 1_000_000;
  const changed = valid && (n !== current.amount || period !== current.period);

  return (
    <form
      className="card flex flex-wrap items-center gap-2 p-4 text-sm"
      onSubmit={async (e) => {
        e.preventDefault();
        if (!changed) return;
        setSaving(true);
        try {
          setCurrent(await api.admin.setAllowanceDefault({ amount: n, period }));
          setSaved(true);
          onSaved();
        } catch (err: any) {
          onError(String(err.message ?? err));
        } finally {
          setSaving(false);
        }
      }}
    >
      <span className="font-medium text-slate-800">Default allowance</span>
      <span className="text-xs text-slate-500">for sellers without their own: listings generated + drafts created</span>
      <input
        inputMode="numeric"
        value={amount}
        onChange={(e) => {
          setAmount(e.target.value);
          setSaved(false);
        }}
        aria-label="Default allowance"
        aria-invalid={!valid}
        className="field w-24 py-1 text-sm tabular-nums sm:ml-auto"
      />
      <select
        value={period}
        onChange={(e) => {
          setPeriod(e.target.value as AllowanceDefault["period"]);
          setSaved(false);
        }}
        aria-label="Default allowance period"
        className="field w-auto py-1 text-sm"
      >
        {PERIODS.map((p) => (
          <option key={p} value={p}>
            {`per ${p === "daily" ? "day" : p === "weekly" ? "week" : "month"}`}
          </option>
        ))}
      </select>
      <button type="submit" disabled={!changed || saving} className="btn-primary px-3 py-1 text-xs">
        {saving ? "Saving…" : "Save"}
      </button>
      {saved && <span key="saved" className="text-xs text-emerald-700">Saved</span>}
    </form>
  );
}
