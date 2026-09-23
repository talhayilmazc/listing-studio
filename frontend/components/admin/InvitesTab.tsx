"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { AdminInvite, InviteIssued, InviteState } from "@/lib/types";
import { Confirm, Reveal } from "./Reveal";

const EXPIRY: { label: string; days: number | null }[] = [
  { label: "7 days", days: 7 },
  { label: "30 days", days: 30 },
  { label: "90 days", days: 90 },
  { label: "Never", days: null },
];

const STATE_STYLE: Record<InviteState, string> = {
  unused: "border-brand-100 bg-brand-50 text-brand-700",
  used: "border-emerald-200 bg-emerald-50 text-emerald-700",
  expired: "border-slate-200 bg-slate-50 text-slate-500",
  revoked: "border-slate-200 bg-slate-50 text-slate-500",
};

function date(iso: string | null) {
  if (!iso) return null;
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

export function InvitesTab({
  invites,
  onCreated,
  onChanged,
  onError,
}: {
  invites: AdminInvite[] | null;
  onCreated: (invite: AdminInvite) => void;
  onChanged: (invite: AdminInvite) => void;
  onError: (message: string) => void;
}) {
  const [email, setEmail] = useState("");
  const [note, setNote] = useState("");
  const [days, setDays] = useState<number | null>(30);
  const [busy, setBusy] = useState(false);
  const [issued, setIssued] = useState<InviteIssued | null>(null);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const res = await api.admin.createInvite({
        email: email.trim() || undefined,
        note: note.trim() || undefined,
        expires_in_days: days,
      });
      setIssued(res);
      onCreated(res.invite);
      setEmail("");
      setNote("");
    } catch (err: any) {
      onError(String(err.message ?? err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <form onSubmit={create} className="card grid gap-4 p-5 md:grid-cols-[1fr_1fr_auto_auto] md:items-end">
        <label className="block">
          <span className="text-xs font-medium text-slate-600">Bind to email (optional)</span>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="Anyone with the code"
            maxLength={254}
            className="field mt-1 w-full"
          />
        </label>
        <label className="block">
          <span className="text-xs font-medium text-slate-600">Note (optional)</span>
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Who it's for"
            maxLength={200}
            className="field mt-1 w-full"
          />
        </label>
        <fieldset>
          <legend className="text-xs font-medium text-slate-600">Expires after</legend>
          <div className="mt-1 inline-flex rounded-lg border border-slate-200 p-0.5">
            {EXPIRY.map((o) => (
              <button
                key={o.label}
                type="button"
                aria-pressed={days === o.days}
                onClick={() => setDays(o.days)}
                className={
                  "rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors " +
                  (days === o.days ? "bg-slate-900 text-white" : "text-slate-600 hover:text-slate-900")
                }
              >
                {o.label}
              </button>
            ))}
          </div>
        </fieldset>
        <button type="submit" disabled={busy} className="btn-primary">
          {busy ? "Generating…" : "Generate code"}
        </button>
      </form>

      {issued && (
        <Reveal
          title="Invite code"
          detail={
            <>
              {issued.invite.bound_email
                ? `Only ${issued.invite.bound_email} can register with it. `
                : "Anyone with the code can register once. "}
              {issued.invite.expires_at
                ? `Expires ${date(issued.invite.expires_at)}.`
                : "Does not expire."}
            </>
          }
          value={issued.code}
          onDismiss={() => setIssued(null)}
        />
      )}

      {!invites ? (
        <div className="card h-48 animate-pulse bg-slate-50" />
      ) : invites.length === 0 ? (
        <div className="card p-8 text-center text-sm text-slate-500">No invite codes yet.</div>
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full min-w-[820px] text-left text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-xs text-slate-400">
                <th className="px-4 py-3 font-medium">State</th>
                <th className="px-4 py-3 font-medium">For</th>
                <th className="px-4 py-3 font-medium">Created</th>
                <th className="px-4 py-3 font-medium">Expires</th>
                <th className="px-4 py-3 font-medium">Used by</th>
                <th className="px-4 py-3 text-right font-medium" />
              </tr>
            </thead>
            <tbody>
              {invites.map((inv) => (
                <tr key={inv.id} className="border-b border-slate-100 last:border-0">
                  <td className="px-4 py-3">
                    <span
                      className={
                        "rounded-md border px-1.5 py-0.5 text-xs font-medium capitalize " +
                        STATE_STYLE[inv.state]
                      }
                    >
                      {inv.state}
                    </span>
                  </td>
                  <td className="max-w-[260px] px-4 py-3">
                    <p className="truncate text-slate-800">{inv.bound_email ?? "Anyone"}</p>
                    {inv.note && <p className="truncate text-xs text-slate-500">{inv.note}</p>}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-slate-600">
                    {date(inv.created_at)}
                    {inv.created_by_email && (
                      <span className="block text-xs text-slate-400">by {inv.created_by_email}</span>
                    )}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-slate-600">
                    {date(inv.expires_at) ?? <span className="text-slate-400">Never</span>}
                  </td>
                  <td className="max-w-[240px] px-4 py-3">
                    {inv.used_by_email ? (
                      <>
                        <p className="truncate text-slate-800">{inv.used_by_email}</p>
                        <p className="text-xs text-slate-400">{date(inv.used_at)}</p>
                      </>
                    ) : (
                      <span className="text-slate-400">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right">
                    {inv.state === "unused" && (
                      <Confirm
                        label="Revoke"
                        confirmLabel="Revoke"
                        tone="danger"
                        onConfirm={async () => {
                          try {
                            onChanged(await api.admin.revokeInvite(inv.id));
                          } catch (err: any) {
                            onError(String(err.message ?? err));
                          }
                        }}
                      />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
