"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { AdminSpend, AdminUser, TempPasswordIssued } from "@/lib/types";
import { Confirm, Reveal } from "./Reveal";
import { Meter } from "./Usage";
import { AllowanceCell, DefaultAllowance } from "./AllowanceControls";

import { Txt } from "@/components/Txt";
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
  onReload,
}: {
  users: AdminUser[] | null;
  selfId: string;
  globalLimit: number;
  onChanged: (user: AdminUser) => void;
  onError: (message: string) => void;
  /** Everyone's allowance may have moved (the default changed): fetch the list again. */
  onReload: () => void;
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

  /** One seller's figures and controls, laid out as a table row or a card. */
  function cells(u: AdminUser) {
    const self = u.id === selfId;
    return {
      account: (
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate font-medium text-slate-900" title={u.email}>
            {u.email}
          </span>
          {u.is_admin && (
            <span key="admin" className="shrink-0 rounded border border-slate-200 px-1 text-[10px] font-medium uppercase tracking-wide text-slate-500">
              admin
            </span>
          )}
          {self && <span key="you" className="shrink-0 text-xs text-slate-400">you</span>}
        </div>
      ),
      meta: (
        <p translate="no" className="mt-0.5 text-xs text-slate-500">
          {`Registered ${date(u.created_at)} · ${u.listings_published.toLocaleString()} published`}
        </p>
      ),
      shops: (
        <div className="space-y-1">
          <ShopsCell
            user={u}
            onSave={async (n) => {
              const next = await run(() => api.admin.setShopLimit(u.id, n));
              if (next) onChanged(next);
              return Boolean(next);
            }}
          />
          <StorageCapCell
            user={u}
            onSave={async (gb) => {
              const next = await run(() => api.admin.setStorageCap(u.id, gb));
              if (next) onChanged(next);
              return Boolean(next);
            }}
          />
        </div>
      ),
      allowance: (
        <AllowanceCell
          user={u}
          onSave={async (amount, period) => {
            const next = await run(() => api.admin.setAllowance(u.id, amount, period));
            if (next) onChanged(next);
            return Boolean(next);
          }}
        />
      ),
      quota: (
        <QuotaCell
          user={u}
          globalLimit={globalLimit}
          onSave={async (n) => {
            const next = await run(() => api.admin.setQuota(u.id, n));
            if (next) onChanged(next);
            return Boolean(next);
          }}
        />
      ),
      trademarks: (
        <div>
          {/* v7 §A4: off lets brand names through, at the seller's own risk. */}
          <select
            className={
              "field w-auto py-1 text-xs " + (u.trademark_filter_effective ? "" : "border-amber-400 text-amber-800")
            }
            value={u.trademark_filter === null ? "default" : u.trademark_filter ? "on" : "off"}
            aria-label={`Trademark filter for ${u.email}`}
            title={
              u.trademark_filter_effective
                ? "Brand and character names are refused in this seller's listings"
                : "Off: brand names may appear; Etsy's IP policy risk is the seller's"
            }
            onChange={async (e) => {
              const v = e.target.value;
              const next = await run(() => api.admin.setTrademarkFilter(u.id, v === "default" ? null : v === "on"));
              if (next) onChanged(next);
            }}
          >
            <option value="default">{`Seller's choice (${u.trademark_filter_seller ? "on" : "off"})`}</option>
            <option value="on">Override: on</option>
            <option value="off">Override: off</option>
          </select>
          <p translate="no" className="mt-1 text-[11px] text-slate-500">
            <span><span>{`Seller set it ${u.trademark_filter_seller ? "on" : "off"}`}</span>
            <span>{u.trademark_filter_changed_at ? ` on ${new Date(u.trademark_filter_changed_at).toLocaleDateString()}` : " (default)"}</span>
            <Txt>{u.trademark_filter !== null ? " · overridden" : ""}</Txt></span>
          </p>
          {/* v7 §B: model new listings on the seller's own. */}
          <label className="mt-1 flex items-center gap-1.5 text-xs text-slate-600 max-sm:min-h-[2.75rem]" title="Let this seller model new listings on their own best listings">
            <input
              type="checkbox"
              checked={Boolean(u.features?.own_patterns)}
              onChange={async (e) => {
                const next = await run(() => api.admin.setFeatures(u.id, { own_patterns: e.target.checked }));
                if (next) onChanged(next);
              }}
            />
            Own-listing patterns
          </label>
        </div>
      ),
      status: <StatusBadge user={u} />,
      actions: (
        <div className="flex flex-wrap items-center gap-1">
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
              key="suspend"
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
              key="reactivate"
              label="Reactivate"
              confirmLabel="Reactivate"
              onConfirm={async () => {
                const next = await run(() => api.admin.reactivate(u.id));
                if (next) onChanged(next);
              }}
            />
          )}
        </div>
      ),
    };
  }

  return (
    <div className="space-y-4">
      {issued && (
        <Reveal key="reveal-43-6"
          title={`Temporary password for ${issued.email}`}
          detail="Their sessions were signed out. They must choose a new password when they next sign in. Send it over a channel you trust."
          value={issued.temporary_password}
          onDismiss={() => setIssued(null)}
        />
      )}

      <DefaultAllowance onSaved={onReload} onError={onError} />

      {/* Wide screens: one row per seller. Narrower (phones, tablets, small
          laptops): one card per seller, every control reachable without
          scrolling sideways; the admin panel is often used from a phone. */}
      <div className="card hidden min-[1400px]:block">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-xs text-slate-400">
              <th className="px-3 py-3 font-medium">Account</th>
              <th className="px-3 py-3 font-medium">Shop</th>
              <th className="px-3 py-3 font-medium" title="Listings generated in the account's period (the plan's limit), and the account's Etsy requests today against its ceiling">
                Listings generated · Etsy requests today
              </th>
              <th className="px-3 py-3 font-medium" title="Refuse brand and character names in this seller's listings">
                Trademarks
              </th>
              <th className="px-3 py-3 text-right font-medium">Status</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => {
              const c = cells(u);
              return (
                <tr key={u.id} className="border-b border-slate-100 align-top last:border-0">
                  <td className="max-w-[260px] px-3 py-3">
                    {c.account}
                    {c.meta}
                  </td>
                  <td className="px-3 py-3">{c.shops}</td>
                  <td className="px-3 py-3">
                    <div className="space-y-2">
                      {c.allowance}
                      <div className="text-[11px] text-slate-400">Etsy requests today</div>
                      {c.quota}
                    </div>
                  </td>
                  <td className="px-3 py-3">{c.trademarks}</td>
                  <td className="px-3 py-3 text-right">
                    <div className="flex flex-col items-end gap-2">
                      {c.status}
                      {c.actions}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <ul className="grid grid-cols-1 gap-3 lg:grid-cols-2 min-[1400px]:hidden" aria-label="Accounts">
        {users.map((u) => {
          const c = cells(u);
          return (
            <li key={u.id} className="card min-w-0 space-y-4 p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  {c.account}
                  {c.meta}
                </div>
                {c.status}
              </div>
              <dl className="grid gap-4 sm:grid-cols-2">
                <Field label="Shops">{c.shops}</Field>
                <Field label="Listings generated">{c.allowance}</Field>
                <Field label="Etsy requests today">{c.quota}</Field>
                <Field label="Trademarks">{c.trademarks}</Field>
              </dl>
              <div className="border-t border-slate-100 pt-3">{c.actions}</div>
            </li>
          );
        })}
      </ul>
      <p className="text-xs text-slate-500">
        Suspending signs the account out everywhere and blocks sign-in until you reactivate it.
        You can see account details and quota here, never a seller&apos;s designs or listings.
      </p>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="mb-1 text-[11px] font-medium uppercase tracking-[0.08em] text-slate-400">{label}</dt>
      <dd>{children}</dd>
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
/**
 * The account's shops (names only) and how many it may connect (v5 §E). The
 * ceiling is editable here; empty resets it to the default.
 */
function ShopsCell({
  user,
  onSave,
}: {
  user: AdminUser;
  onSave: (n: number | null) => Promise<boolean>;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(user.shops_limit_custom ? String(user.shops_limit) : "");
  const n = value.trim() === "" ? null : Number(value);
  const valid = n === null || (Number.isInteger(n) && n >= 1);

  return (
    <div className="min-w-[160px] space-y-1">
      {user.shops.length ? (
        <p className="truncate text-slate-700" title={user.shops.join(", ")}>
          <span aria-hidden className="mr-1.5 inline-block h-1.5 w-1.5 rounded-full bg-emerald-500 align-middle" />
          <span>{user.shops.join(", ")}</span>
        </p>
      ) : (
        <p className="text-slate-400">Not connected</p>
      )}
      {editing ? (
        <form
          className="flex items-center gap-1.5"
          onSubmit={async (e) => {
            e.preventDefault();
            if (!valid) return;
            if (await onSave(n)) setEditing(false);
          }}
        >
          <input
            autoFocus
            inputMode="numeric"
            value={value}
            placeholder="default"
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => e.key === "Escape" && setEditing(false)}
            aria-label={`Shop limit for ${user.email}`}
            aria-invalid={!valid}
            className="field w-16 py-0.5 text-xs tabular-nums"
          />
          <button type="submit" disabled={!valid} className="btn-primary px-2 py-0.5 text-xs">
            Save
          </button>
          <button type="button" onClick={() => setEditing(false)} className="px-1 text-xs text-slate-500">
            Cancel
          </button>
        </form>
      ) : (
        <button translate="no"
          type="button"
          onClick={() => setEditing(true)}
          title="Change how many shops this account may connect"
          className="tap text-xs tabular-nums text-slate-500 underline decoration-slate-300 decoration-dotted underline-offset-2 hover:text-slate-900 max-sm:py-2"
        >
          <span><span>{user.shops_used}</span><span> of </span><span>{user.shops_limit}</span><span> shops</span>
          <Txt>{user.shops_limit_custom ? " (custom)" : ""}</Txt></span>
        </button>
      )}
    </div>
  );
}

/** The account's cap on stored image files (core/storage_cap.py); empty = the default. */
function StorageCapCell({ user, onSave }: { user: AdminUser; onSave: (gb: number | null) => Promise<boolean> }) {
  const gb = (bytes: number) => Math.round((bytes / 1024 ** 3) * 10) / 10;
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(user.storage_cap_custom ? String(gb(user.storage_cap_bytes)) : "");
  const n = value.trim() === "" ? null : Number(value);
  const valid = n === null || (Number.isFinite(n) && n > 0 && n <= 1000);
  if (editing) {
    return (
      <form
        className="flex items-center gap-1.5"
        onSubmit={async (e) => {
          e.preventDefault();
          if (valid && (await onSave(n))) setEditing(false);
        }}
      >
        <input
          autoFocus
          inputMode="decimal"
          value={value}
          placeholder="default"
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => e.key === "Escape" && setEditing(false)}
          aria-label={`Storage cap in GB for ${user.email}`}
          aria-invalid={!valid}
          className="field w-16 py-0.5 text-xs tabular-nums"
        />
        <span className="text-xs text-slate-500">GB</span>
        <button type="submit" disabled={!valid} className="btn-primary px-2 py-0.5 text-xs">Save</button>
        <button type="button" onClick={() => setEditing(false)} className="px-1 text-xs text-slate-500">Cancel</button>
      </form>
    );
  }
  return (
    <button translate="no"
      type="button"
      onClick={() => setEditing(true)}
      title="Change how much image storage this account may use; uploads past it are refused"
      className="tap text-xs tabular-nums text-slate-500 underline decoration-slate-300 decoration-dotted underline-offset-2 hover:text-slate-900 max-sm:py-2"
    >
      <span><span>{gb(user.storage_cap_bytes)}</span><span> GB storage cap</span>
      <Txt>{user.storage_cap_custom ? " (custom)" : ""}</Txt></span>
    </button>
  );
}

function QuotaCell({
  user,
  globalLimit,
  onSave,
}: {
  user: AdminUser;
  globalLimit: number;
  /** null: the account follows the default again. */
  onSave: (n: number | null) => Promise<boolean>;
}) {
  const etsy = user.etsy;
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(String(etsy.limit));
  const [saving, setSaving] = useState(false);

  const n = Number(value);
  const valid = value.trim() !== "" && Number.isInteger(n) && n >= 0 && n <= globalLimit;

  async function save(next: number | null) {
    setSaving(true);
    const ok = await onSave(next);
    setSaving(false);
    if (ok) setEditing(false);
  }

  if (editing) {
    return (
      <form
        className="flex flex-wrap items-center gap-1.5"
        onSubmit={(e) => {
          e.preventDefault();
          if (valid) save(n);
        }}
      >
        <input
          autoFocus
          inputMode="numeric"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => e.key === "Escape" && setEditing(false)}
          aria-label={`Etsy requests per day for ${user.email}`}
          aria-invalid={!valid}
          className="field w-20 py-1 text-sm tabular-nums"
        />
        <button type="submit" disabled={!valid || saving} className="btn-primary px-2 py-1 text-xs">
          {saving ? "…" : "Save"}
        </button>
        {!etsy.follows_default && (
          <button
            key="default"
            type="button"
            disabled={saving}
            onClick={() => save(null)}
            className="tap px-1 text-xs text-slate-500 underline hover:text-slate-800"
            title={`Remove this account's own number: it follows the default (${etsy.default.toLocaleString()} a day)`}
          >
            <span translate="no">{`Follow default (${etsy.default.toLocaleString()})`}</span>
          </button>
        )}
        <button
          type="button"
          onClick={() => setEditing(false)}
          className="px-1 text-xs text-slate-500 hover:text-slate-800"
        >
          Cancel
        </button>
        {!valid && (
          <span key="span-348-8" className="text-xs text-rose-600"><span>0–<span>{globalLimit.toLocaleString()}</span></span></span>
        )}
      </form>
    );
  }

  return (
    <div className="min-w-[180px] space-y-1.5">
    <div className="flex items-center gap-2.5">
      <div className="w-20">
        <Meter used={etsy.used} limit={etsy.limit} label={`${user.email} Etsy requests today`} />
      </div>
      <button translate="no"
        type="button"
        onClick={() => {
          setValue(String(etsy.limit));
          setEditing(true);
        }}
        title="Change this account's Etsy requests per day, or put it back on the default"
        className="tap rounded px-1 text-xs tabular-nums text-slate-600 underline decoration-slate-300 decoration-dotted underline-offset-2 hover:text-slate-900 max-sm:py-2"
      >
        <span><span>{etsy.used.toLocaleString()}</span> / <span>{etsy.limit.toLocaleString()}</span></span>
      </button>
    </div>
    <p translate="no" className="whitespace-nowrap text-[11px] text-slate-400" title="Etsy's day: both Etsy counters reset at 00:00 UTC">
      {`${etsy.follows_default ? "default" : "own limit"} · ${etsy.remaining.toLocaleString()} left · resets 00:00 UTC (${etsy.resets_label} for the seller)`}
    </p>
    <Spend today={user.spent_today ?? []} yesterday={user.spent_yesterday ?? []} />
    </div>
  );
}

function Spend({ today, yesterday }: { today: AdminSpend[]; yesterday: AdminSpend[] }) {
  const [day, setDay] = useState<"today" | "yesterday">("today");
  const rows = day === "today" ? today : yesterday;
  const counted = rows.filter((r) => r.counted > 0).sort((a, b) => b.counted - a.counted);
  const upkeep = rows.filter((r) => r.upkeep > 0).sort((a, b) => b.upkeep - a.upkeep);
  const sum = (list: AdminSpend[], k: "counted" | "upkeep") => list.reduce((n, r) => n + r[k], 0);
  if (!today.length && !yesterday.length) {
    return <p className="text-[11px] text-slate-400">No Etsy requests today or yesterday</p>;
  }
  return (
    <div className="space-y-1 text-[11px] leading-snug text-slate-500" translate="no">
      <SpendGroup
        title="Counts toward the account's ceiling"
        total={sum(counted, "counted")}
        items={counted.map((r) => [r.label, r.counted])}
      />
      <SpendGroup
        title="App upkeep, not counted"
        total={sum(upkeep, "upkeep")}
        items={upkeep.map((r) => [r.label, r.upkeep])}
      />
      <button
        type="button"
        onClick={() => setDay(day === "today" ? "yesterday" : "today")}
        className="tap text-slate-500 underline decoration-slate-300 decoration-dotted underline-offset-2 hover:text-slate-900 max-sm:py-2"
      >
        <span>{day === "today" ? "Showing today (UTC) · see yesterday" : "Showing yesterday (UTC) · see today"}</span>
      </button>
    </div>
  );
}

function SpendGroup({ title, total, items }: { title: string; total: number; items: [string, number][] }) {
  return (
    <div>
      <p className="text-slate-400">
        <span>{title}</span><span>: </span><span className="tabular-nums text-slate-600">{total.toLocaleString()}</span>
      </p>
      <ul className="pl-2">
        {items.map(([label, n]) => (
          <li key={label} className="flex justify-between gap-3">
            <span>{label}</span>
            <span className="tabular-nums text-slate-700">{n.toLocaleString()}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
