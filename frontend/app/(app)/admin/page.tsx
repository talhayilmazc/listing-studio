"use client";

import { notFound } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { AdminDisk, AdminInvite, AdminUsage, AdminUser, AiCost } from "@/lib/types";
import { useSession } from "@/components/SessionProvider";
import { UsersTab } from "@/components/admin/UsersTab";
import { InvitesTab } from "@/components/admin/InvitesTab";
import { OperatorSettings } from "@/components/admin/OperatorSettings";
import { InviteRequests } from "@/components/admin/InviteRequests";
import { AiCostPanel } from "@/components/admin/AiCost";
import { DiskPanel } from "@/components/admin/Disk";
import { SalesRereadAdminPanel } from "@/components/analytics/SalesReread";
import { Txt } from "@/components/Txt";
import { UsageSummary, UsageTab } from "@/components/admin/Usage";

/**
 * Operator screens. `is_admin` here only decides what to paint: every request
 * below is re-checked on the server, which answers 404 to anyone else — the same
 * answer a non-admin gets from this page.
 *
 * The data is account metadata and quota only. Nothing here reads, or could
 * read, another seller's designs, batches or generated content.
 */

type Tab = "users" | "invites" | "usage";
const TABS: { id: Tab; label: string }[] = [
  { id: "users", label: "Users" },
  { id: "invites", label: "Invites" },
  { id: "usage", label: "Usage" },
];

// The budget moves as sellers work; keep the at-a-glance figure current.
const USAGE_REFRESH_MS = 30_000;

export default function AdminPage() {
  const { account, loading } = useSession();
  const [tab, setTab] = useState<Tab>("users");
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [invites, setInvites] = useState<AdminInvite[] | null>(null);
  const [pendingRequests, setPendingRequests] = useState<number | null>(null);
  const [aiCost, setAiCost] = useState<AiCost | null>(null);
  const [disk, setDisk] = useState<AdminDisk | null>(null);
  const [usage, setUsage] = useState<AdminUsage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [denied, setDenied] = useState(false);

  const isAdmin = Boolean(account?.is_admin);

  const guard = useCallback((e: unknown) => {
    // The role was withdrawn mid-visit: become the 404 the server says we are.
    if (e instanceof ApiError && e.status === 404) setDenied(true);
    else setError(String((e as Error)?.message ?? e));
  }, []);

  const loadUsage = useCallback(
    // Etsy requests and AI cost move as sellers work: both are refreshed together.
    () => Promise.all([api.admin.usage().then(setUsage), api.admin.aiCost().then(setAiCost), api.admin.disk().then(setDisk)]).catch(guard),
    [guard],
  );

  useEffect(() => {
    if (!isAdmin) return;
    api.admin.users().then(setUsers).catch(guard);
    api.admin.invites().then(setInvites).catch(guard);
    // The badge on the Invites tab: requests waiting, known before the tab is opened.
    const loadPending = () => api.admin.pendingInviteRequests().then((r) => setPendingRequests(r.pending)).catch(guard);
    loadPending();
    loadUsage();
    const timer = setInterval(() => {
      loadUsage();
      loadPending();
    }, USAGE_REFRESH_MS);
    return () => clearInterval(timer);
  }, [isAdmin, guard, loadUsage]);

  if (loading) return <AdminSkeleton />;
  if (!isAdmin || denied) notFound();

  return (
    <div className="space-y-6">
      <UsageSummary usage={usage} />

      {error && (
        <div key="div-69-6"
          role="alert"
          className="flex items-start justify-between gap-4 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700"
        >
          <span>{error}</span>
          <button type="button" onClick={() => setError(null)} className="shrink-0 text-xs hover:underline">
            Dismiss
          </button>
        </div>
      )}

      <div>
        {/* Each count sits in a slot of fixed width that is there from the first paint, and the
            pending-requests badge has its own slot whether or not there is anything waiting:
            a number arriving, or changing from 9 to 10, moves no tab. If the bar is ever wider
            than a narrow screen it scrolls inside itself, not the page. */}
        <div role="tablist" aria-label="Admin sections" className="flex gap-4 overflow-x-auto border-b border-slate-200 sm:gap-6" data-testid="admin-tabs">
          {TABS.map((t) => (
            <button
              key={t.id}
              role="tab"
              type="button"
              id={`tab-${t.id}`}
              aria-selected={tab === t.id}
              aria-controls={`panel-${t.id}`}
              onClick={() => setTab(t.id)}
              className={
                "-mb-px flex shrink-0 items-center whitespace-nowrap border-b-2 pb-2.5 text-sm font-medium transition-colors max-sm:min-h-[2.75rem] max-sm:px-1 max-sm:pt-2 " +
                (tab === t.id
                  ? "border-brand-600 text-slate-900"
                  : "border-transparent text-slate-500 hover:text-slate-800")
              }
            >
              <span>{t.label}</span>
              {t.id === "users" && (
                <span translate="no" key="users-count" className="ml-1.5 inline-block w-[2.5ch] text-left text-xs tabular-nums text-slate-400">
                  <Txt>{users ? String(users.length) : ""}</Txt>
                </span>
              )}
              {t.id === "invites" && (
                <span translate="no" key="invites-open" className="ml-1.5 inline-block w-[7.5ch] text-left text-xs tabular-nums text-slate-400">
                  <Txt>{invites ? `${invites.filter((i) => i.state === "unused").length} open` : ""}</Txt>
                </span>
              )}
              {t.id === "invites" && (
                // Requests waiting for a decision: a badge that is there before the tab is opened.
                // Its slot is always there; only what is in it changes.
                <span key="pending-slot" className="ml-1 inline-flex w-8 justify-center" data-testid="pending-requests-slot">
                  {pendingRequests ? (
                    <span translate="no" key="pending-badge" data-testid="pending-requests-badge"
                      className="inline-flex min-w-[1.25rem] items-center justify-center rounded-full bg-brand-600 px-1.5 text-[11px] font-semibold leading-5 text-white"
                      aria-label={`${pendingRequests} invite request${pendingRequests === 1 ? "" : "s"} waiting`}>
                      {pendingRequests > 99 ? "99+" : pendingRequests}
                    </span>
                  ) : null}
                </span>
              )}
            </button>
          ))}
        </div>

        {/* All three panels are mounted from the start and only hidden: each loads its own
            data once, while the page loads, so opening a tab shows what is already there
            instead of a placeholder that then jumps to its real height. The panel area keeps
            a minimum height, so the footer does not leap up under a short tab. */}
        <div className="min-h-[70vh] pt-5" data-testid="admin-panels">
          <div role="tabpanel" id="panel-users" aria-labelledby="tab-users" hidden={tab !== "users"}>
            <UsersTab key="userstab-113-10"
              users={users}
              selfId={account!.id}
              globalLimit={usage?.global_limit ?? 100000}
              onError={setError}
              onChanged={(u) => {
                setUsers((prev) => prev?.map((x) => (x.id === u.id ? u : x)) ?? prev);
                loadUsage();
              }}
              onReload={() => api.admin.users().then(setUsers).catch(guard)}
            />
          </div>
          <div role="tabpanel" id="panel-invites" aria-labelledby="tab-invites" hidden={tab !== "invites"}>
            <div key="requests" className="mb-5">
              <InviteRequests
                onError={setError}
                onCount={setPendingRequests}
                onInvite={(inv) => setInvites((prev) => [inv, ...(prev ?? [])])}
              />
            </div>
            <InvitesTab key="invitestab-125-10"
              invites={invites}
              onError={setError}
              onCreated={(inv) => setInvites((prev) => [inv, ...(prev ?? [])])}
              onChanged={(inv) =>
                setInvites((prev) => prev?.map((x) => (x.id === inv.id ? inv : x)) ?? prev)
              }
            />
          </div>
          <div role="tabpanel" id="panel-usage" aria-labelledby="tab-usage" hidden={tab !== "usage"}>
            <div key="operator" className="mb-5">
              <OperatorSettings onError={setError} />
            </div>
            <div key="disk" className="mb-5">
              <DiskPanel disk={disk} onChanged={loadUsage} onError={setError} />
            </div>
            <div key="reread" className="mb-5 empty:hidden">
              <SalesRereadAdminPanel />
            </div>
            <div key="aicost" className="mb-5">
              <AiCostPanel cost={aiCost} onChanged={loadUsage} onError={setError} />
            </div>
            <UsageTab key="usagetab-135-10" usage={usage} />
          </div>
        </div>
      </div>
    </div>
  );
}

function AdminSkeleton() {
  return (
    <div className="space-y-6">
      <div className="card h-40 animate-pulse bg-slate-50" />
      <div className="card h-64 animate-pulse bg-slate-50" />
    </div>
  );
}
