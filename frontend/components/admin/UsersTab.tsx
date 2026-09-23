"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { AdminUser, TempPasswordIssued } from "@/lib/types";
import { Confirm, Reveal } from "./Reveal";
import { Meter } from "./Usage";

const SELF_REASON = "You can't do this to your own account";

function date(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

export function UsersTab({
  users,
  selfId,
  globalLimit,
  onChanged,
  onError,
}: {
  users: AdminUser[] | null;
  selfId: string;
  globalLimit: number;
  onChanged: (user: AdminUser) => void;
  onError: (message: string) => void;
}) {
  const [issued, setIssued] = useState<TempPasswordIssued | null>(null);

  async function run<T>(fn: () => Promise<T>): Promise<T | undefined> {
    try {
      return await fn();
    } catch (e: any) {
      onError(String(e.message ?? e));
      return undefined;
    }
  }

  if (!users) return <div className="card h-64 animate-pulse bg-slate-50" />;

  return (
    <div className="space-y-4">
      {issued && (
        <Reveal
          title={`Temporary password for ${issued.email}`}
          detail="Their sessions were signed out. They must choose a new password when they next sign in. Send it over a channel you trust."
          value={issued.temporary_password}
          onDismiss={() => setIssued(null)}
        />
      )}

      <div className="card overflow-x-auto">
        <table className="w-full min-w-[900px] text-left text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-xs text-slate-400">
              <th className="px-3 py-3 font-medium">Account</th>
              <th className="px-3 py-3 font-medium">Shop</th>
              <th className="px-3 py-3 font-medium">Registered</th>
              <th className="px-3 py-3 text-right font-medium">Published</th>
              <th className="px-3 py-3 font-medium">Quota today</th>
              <th className="px-3 py-3 font-medium">Status</th>
              <th className="px-3 py-3 text-right font-medium">Actions</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => {
              const self = u.id === selfId;
              return (
                <tr key={u.id} className="border-b border-slate-100 align-middle last:border-0">
                  <td className="max-w-[240px] px-3 py-3">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-slate-900" title={u.email}>
                        {u.email}
                      </span>
                      {u.is_admin && (
                        <span className="shrink-0 rounded border border-slate-200 px-1 text-[10px] font-medium uppercase tracking-wide text-slate-500">
                          admin
                        </span>
                      )}
                      {self && <span className="shrink-0 text-xs text-slate-400">you</span>}
                    </div>
                  </td>
                  <td className="whitespace-nowrap px-3 py-3">
                    {u.shop_connected ? (
                      <span className="inline-flex items-center gap-1.5 text-slate-700">
                        <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                        {u.shop_name ?? "Connected"}
                      </span>
                    ) : (
                      <span className="text-slate-400">Not connected</span>
                    )}
                  </td>
                  <td className="whitespace-nowrap px-3 py-3 text-slate-600">{date(u.created_at)}</td>
                  <td className="px-3 py-3 text-right tabular-nums text-slate-700">
                    {u.listings_published.toLocaleString()}
                  </td>
                  <td className="whitespace-nowrap px-3 py-3">
                    <QuotaCell
                      user={u}
                      globalLimit={globalLimit}
                      onSave={async (n) => {
                        const next = await run(() => api.admin.setQuota(u.id, n));
                        if (next) onChanged(next);
                        return Boolean(next);
                      }}
                    />
                  </td>
                  <td className="whitespace-nowrap px-3 py-3">
                    <StatusBadge user={u} />
                  </td>
                  <td className="whitespace-nowrap px-3 py-3 text-right">
                    <div className="inline-flex items-center gap-1">
                      <Confirm
                        label="Temp password"
                        confirmLabel="Issue"
                        disabled={self}
                        disabledReason={SELF_REASON}
                        onConfirm={async () => {
                          const res = await run(() => api.admin.temporaryPassword(u.id));
                          if (res) {
                            setIssued(res);
                            onChanged({ ...u, must_change_password: true });
                          }
                        }}
                      />
                      {u.status === "active" ? (
                        <Confirm
                          label="Suspend"
                          confirmLabel="Suspend"
                          tone="danger"
                          disabled={self}
                          disabledReason={SELF_REASON}
                          onConfirm={async () => {
                            const next = await run(() => api.admin.suspend(u.id));
                            if (next) onChanged(next);
                          }}
                        />
                      ) : (
                        <Confirm
                          label="Reactivate"
                          confirmLabel="Reactivate"
                          onConfirm={async () => {
                            const next = await run(() => api.admin.reactivate(u.id));
                            if (next) onChanged(next);
                          }}
                        />
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-slate-500">
        Suspending signs the account out everywhere and blocks sign-in until you reactivate it.
        You can see account details and quota here, never a seller&apos;s designs or listings.
      </p>
    </div>
  );
}

function StatusBadge({ user }: { user: AdminUser }) {
  if (user.status === "suspended") {
    return (
      <span className="rounded-md border border-rose-200 bg-rose-50 px-1.5 py-0.5 text-xs font-medium text-rose-700">
        Suspended
      </span>
    );
  }
  if (user.must_change_password) {
    return (
      <span className="rounded-md border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-xs font-medium text-amber-700">
        Temp password
      </span>
    );
  }
  return (
    <span className="rounded-md border border-emerald-200 bg-emerald-50 px-1.5 py-0.5 text-xs font-medium text-emerald-700">
      Active
    </span>
  );
}

/** Used today against this user's own ceiling, with the ceiling editable in place. */
function QuotaCell({
  user,
  globalLimit,
  onSave,
}: {
  user: AdminUser;
  globalLimit: number;
  onSave: (n: number) => Promise<boolean>;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(String(user.daily_quota));
  const [saving, setSaving] = useState(false);

  const n = Number(value);
  const valid = value.trim() !== "" && Number.isInteger(n) && n >= 0 && n <= globalLimit;

  if (editing) {
    return (
      <form
        className="flex items-center gap-1.5"
        onSubmit={async (e) => {
          e.preventDefault();
          if (!valid) return;
          setSaving(true);
          const ok = await onSave(n);
          setSaving(false);
          if (ok) setEditing(false);
        }}
      >
        <input
          autoFocus
          inputMode="numeric"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => e.key === "Escape" && setEditing(false)}
          aria-label={`Daily request ceiling for ${user.email}`}
          aria-invalid={!valid}
          className="field w-20 py-1 text-sm tabular-nums"
        />
        <button type="submit" disabled={!valid || saving} className="btn-primary px-2 py-1 text-xs">
          {saving ? "…" : "Save"}
        </button>
        <button
          type="button"
          onClick={() => setEditing(false)}
          className="px-1 text-xs text-slate-500 hover:text-slate-800"
        >
          Cancel
        </button>
        {!valid && (
          <span className="text-xs text-rose-600">0–{globalLimit.toLocaleString()}</span>
        )}
      </form>
    );
  }

  return (
    <div className="flex min-w-[180px] items-center gap-2.5">
      <div className="w-20">
        <Meter used={user.quota_used_today} limit={user.daily_quota} label={`${user.email} quota used today`} />
      </div>
      <button
        type="button"
        onClick={() => {
          setValue(String(user.daily_quota));
          setEditing(true);
        }}
        title="Change this user's daily ceiling"
        className="rounded px-1 text-xs tabular-nums text-slate-600 underline decoration-slate-300 decoration-dotted underline-offset-2 hover:text-slate-900"
      >
        {user.quota_used_today.toLocaleString()} / {user.daily_quota.toLocaleString()}
      </button>
    </div>
  );
}
