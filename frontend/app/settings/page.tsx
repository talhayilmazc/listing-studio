"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { browserZone, formatWhen, isValidZone } from "@/lib/schedule";
import { useSession } from "@/components/SessionProvider";
import { TrademarkFilterSetting } from "@/components/TrademarkFilterSetting";
import { AllowanceSection } from "@/components/Allowance";

/** Every IANA zone this browser knows, falling back to a few if it can't list them. */
function allZones(): string[] {
  try {
    return (Intl as unknown as { supportedValuesOf(k: string): string[] }).supportedValuesOf("timeZone");
  } catch {
    return ["UTC", "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles", "Europe/London", "Europe/Istanbul"];
  }
}

/** Account settings: the time zone schedules use, the trademark filter, and the password. */
export default function SettingsPage() {
  const { account, timeZone, setAccount } = useSession();
  const [zone, setZone] = useState(timeZone);
  const [here, setHere] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const zones = useMemo(allZones, []);

  useEffect(() => setZone(timeZone), [timeZone]);
  useEffect(() => setHere(browserZone()), []);

  async function save() {
    if (!isValidZone(zone)) return setMessage({ ok: false, text: "Choose a time zone from the list." });
    setSaving(true);
    setMessage(null);
    try {
      setAccount(await api.setTimeZone(zone));
      setMessage({ ok: true, text: "Saved. Schedules are shown in this zone from now on; each keeps the moment it was set for." });
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : "Could not save." });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="max-w-2xl space-y-6">
      <section className="card space-y-4 p-5">
        <div>
          <h2 className="text-sm font-semibold text-slate-800">Time zone</h2>
          <p className="mt-1 text-sm text-slate-500">
            Scheduled go-lives are entered and shown in this zone, whatever computer you use. It was taken
            from your browser when you first signed in.
          </p>
        </div>
        <label className="block">
          <span className="label">Your time zone</span>
          <input
            list="zones"
            className="field"
            value={zone}
            onChange={(e) => setZone(e.target.value)}
            spellCheck={false}
          />
          <datalist id="zones">
            {zones.map((z) => (
              <option key={z} value={z} />
            ))}
          </datalist>
        </label>
        {isValidZone(zone) && (
          <p key="now" translate="no" className="text-xs text-slate-500">
            {`Now there: ${formatWhen(new Date(), zone)}`}
          </p>
        )}
        {here && here !== zone && (
          <p key="here" className="text-xs text-amber-800">
            <span>This computer is set to </span>
            <span translate="no">{here.replace(/_/g, " ")}</span>
            <span>. </span>
            <button type="button" className="underline" onClick={() => setZone(here)}>
              Use it
            </button>
          </p>
        )}
        <div className="flex items-center gap-3">
          <button type="button" className="btn-primary" onClick={save} disabled={saving || zone === account?.time_zone}>
            {saving ? "Saving…" : "Save"}
          </button>
          {message && (
            <p key="msg" className={`text-sm ${message.ok ? "text-emerald-700" : "text-rose-700"}`}>
              {message.text}
            </p>
          )}
        </div>
      </section>

      <AllowanceSection />

      <TrademarkFilterSetting />

      <section className="card p-5">
        <h2 className="text-sm font-semibold text-slate-800">Password</h2>
        <p className="mt-1 text-sm text-slate-500">
          <Link href="/password" className="text-brand-700 underline">
            Change your password
          </Link>
        </p>
      </section>
    </div>
  );
}
