"use client";

import { notFound } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { AdminInvite, AdminUsage, AdminUser, AiCost } from "@/lib/types";
import { useSession } from "@/components/SessionProvider";
import { UsersTab } from "@/components/admin/UsersTab";
import { InvitesTab } from "@/components/admin/InvitesTab";
import { InviteRequests } from "@/components/admin/InviteRequests";
import { AiCostPanel } from "@/components/admin/AiCost";
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
    () => Promise.all([api.admin.usage().then(setUsage), api.admin.aiCost().then(setAiCost)]).catch(guard),
    [guard],
  );

  useEffect(() => {
    if (!isAdmin) return;
    api.admin.users().then(setUsers).catch(guard);
    api.admin.invites().then(setInvites).catch(guard);
    loadUsage();
    const timer = setInterval(loadUsage, USAGE_REFRESH_MS);
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
        <div role="tablist" aria-label="Admin sections" className="flex gap-6 border-b border-slate-200">
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
                "-mb-px border-b-2 pb-2.5 text-sm font-medium transition-colors max-sm:min-h-[2.75rem] max-sm:px-2 max-sm:pt-2 " +
                (tab === t.id
                  ? "border-brand-600 text-slate-900"
                  : "border-transparent text-slate-500 hover:text-slate-800")
              }
            >
              <span>{t.label}</span>
              {t.id === "users" && users && (
                <span translate="no" key="span-100-14" className="ml-1.5 text-xs tabular-nums text-slate-400">{users.length}</span>
              )}
              {t.id === "invites" && invites && (
                <span translate="no" key="span-103-14" className="ml-1.5 text-xs tabular-nums text-slate-400">
                  <span><span>{invites.filter((i) => i.state === "unused").length}</span> open</span>
                  <Txt>{pendingRequests ? ` · ${pendingRequests} requested` : ""}</Txt>
                </span>
              )}
            </button>
          ))}
        </div>

        <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} className="pt-5">
          {tab === "users" && (
            <UsersTab key="userstab-113-10"
              users={users}
              selfId={account!.id}
              globalLimit={usage?.global_limit ?? 5000}
              onError={setError}
              onChanged={(u) => {
                setUsers((prev) => prev?.map((x) => (x.id === u.id ? u : x)) ?? prev);
                loadUsage();
              }}
              onReload={() => api.admin.users().then(setUsers).catch(guard)}
            />
          )}
          {tab === "invites" && (
            <div key="requests" className="mb-5">
              <InviteRequests
                onError={setError}
                onCount={setPendingRequests}
                onInvite={(inv) => setInvites((prev) => [inv, ...(prev ?? [])])}
              />
            </div>
          )}
          {tab === "invites" && (
            <InvitesTab key="invitestab-125-10"
              invites={invites}
              onError={setError}
              onCreated={(inv) => setInvites((prev) => [inv, ...(prev ?? [])])}
              onChanged={(inv) =>
                setInvites((prev) => prev?.map((x) => (x.id === inv.id ? inv : x)) ?? prev)
              }
            />
          )}
          {tab === "usage" && (
            <div key="aicost" className="mb-5">
              <AiCostPanel cost={aiCost} onChanged={loadUsage} onError={setError} />
            </div>
          )}
          {tab === "usage" && <UsageTab key="usagetab-135-10" usage={usage} />}
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
