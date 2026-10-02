"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { useSession } from "./SessionProvider";

/** The wording's version; the server records it with the change. Change both together. */
const STATEMENT_VERSION = "2026-09-27";

/**
 * The seller's own trademark filter (v7 §A4). On by default. Turning it off
 * shows plainly what the seller is accepting and needs their confirmation;
 * turning it back on is immediate. While an administrator has set it for the
 * account, it is shown but can't be changed here.
 */
export function TrademarkFilterSetting() {
  const { account, setAccount } = useSession();
  const [confirming, setConfirming] = useState(false);
  const [accepted, setAccepted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!account) return null;

  const on = account.trademark_filter_effective ?? true;
  const locked = Boolean(account.trademark_filter_by_admin);

  async function change(enabled: boolean) {
    setBusy(true);
    setError(null);
    try {
      setAccount(await api.setTrademarkFilter(enabled, !enabled && accepted, enabled ? undefined : STATEMENT_VERSION));
      setConfirming(false);
      setAccepted(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not change the filter.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card space-y-4 p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-sm font-semibold text-slate-800">Trademark filter</h2>
          <p className="mt-1 text-sm text-slate-500">
            On, brand, character and franchise names are kept out of your titles, tags and descriptions, and a
            listing whose design shows a recognisable character can&apos;t be published until you deal with it.
          </p>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={on}
          aria-label="Trademark filter"
          disabled={busy || locked}
          onClick={() => (on ? setConfirming(true) : change(true))}
          className={
            "tap relative mt-0.5 inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors disabled:opacity-60 " +
            (on ? "bg-brand-600" : "bg-slate-300")
          }
        >
          <span
            aria-hidden
            className={"inline-block h-5 w-5 rounded-full bg-white shadow transition-transform " + (on ? "translate-x-5" : "translate-x-0.5")}
          />
        </button>
      </div>

      <p className="text-xs text-slate-500">
        <span className={"font-medium " + (on ? "text-emerald-700" : "text-amber-800")}>{on ? "On" : "Off"}</span>
        {locked ? (
          <span key="locked">. Set by an administrator for your account; ask support to change it.</span>
        ) : account.trademark_filter_changed_at ? (
          <span key="changed">{`. You changed it on ${new Date(account.trademark_filter_changed_at).toLocaleDateString()}.`}</span>
        ) : (
          <span key="default">. On by default.</span>
        )}
      </p>
      {!on && (
        <p key="still-warned" className="rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-900">
          With the filter off, a design that appears to show a recognisable character is still flagged on its card
          before you publish it, as a warning.
        </p>
      )}

      {confirming && (
        <div key="confirm" role="alertdialog" aria-labelledby="tm-confirm-title" className="space-y-3 rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-950">
          <p id="tm-confirm-title" className="font-semibold">Before you turn the filter off</p>
          <ul className="list-disc space-y-1 pl-5">
            <li>
              Etsy&apos;s Intellectual Property Policy prohibits using trademarks, brand names and copyrighted characters
              without the owner&apos;s permission.
            </li>
            <li>Rights holders send Etsy takedown notices, often for many listings at once.</li>
            <li>
              Etsy removes the listings named in those notices and may suspend or close shops that receive them
              repeatedly.
            </li>
            <li>With the filter off, the app no longer keeps these names out of what it writes for you.</li>
          </ul>
          <label className="flex items-start gap-2">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={accepted}
              onChange={(e) => setAccepted(e.target.checked)}
            />
            <span>
              I own or have permission to use the trademarks and characters in my designs, or I accept the risk of
              removed listings and a suspended shop.
            </span>
          </label>
          <div className="flex gap-2">
            <button type="button" className="btn-primary" disabled={!accepted || busy} onClick={() => change(false)}>
              {busy ? "Turning off…" : "Turn the filter off"}
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => {
                setConfirming(false);
                setAccepted(false);
              }}
            >
              Keep it on
            </button>
          </div>
        </div>
      )}
      {error && <p key="error" className="text-sm text-rose-700">{error}</p>}
    </section>
  );
}
