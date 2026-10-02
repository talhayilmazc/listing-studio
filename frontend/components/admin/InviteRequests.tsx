"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { AdminInvite, InviteRequest, InviteRequestApproved } from "@/lib/types";
import { Confirm, Reveal } from "./Reveal";

function date(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

/**
 * Requests from the public "Request an invite" form. Approving one makes an
 * invite code only that address can redeem (30 days); there is no email
 * service, so the code is shown once here for the admin to send.
 */
export function InviteRequests({
  onInvite,
  onError,
  onCount,
}: {
  onInvite: (invite: AdminInvite) => void;
  onError: (message: string) => void;
  onCount?: (pending: number) => void;
}) {
  const [rows, setRows] = useState<InviteRequest[] | null>(null);
  const [approved, setApproved] = useState<InviteRequestApproved | null>(null);
  const [showDecided, setShowDecided] = useState(false);

  useEffect(() => {
    api.admin
      .inviteRequests()
      .then(setRows)
      .catch((e) => onError(String(e.message ?? e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const pending = rows?.filter((r) => r.status === "pending") ?? [];
  const decided = rows?.filter((r) => r.status !== "pending") ?? [];
  useEffect(() => {
    if (rows) onCount?.(pending.length);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows]);

  const replace = (row: InviteRequest) => setRows((prev) => prev?.map((r) => (r.id === row.id ? row : r)) ?? prev);

  async function approve(row: InviteRequest) {
    try {
      const res = await api.admin.approveInviteRequest(row.id);
      replace(res.request);
      onInvite(res.invite);
      setApproved(res);
    } catch (e: any) {
      onError(String(e.message ?? e));
    }
  }
  async function decline(row: InviteRequest) {
    try {
      replace(await api.admin.declineInviteRequest(row.id));
    } catch (e: any) {
      onError(String(e.message ?? e));
    }
  }

  return (
    <section className="card p-5" aria-labelledby="requests-title">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="requests-title" className="font-display text-2xl text-slate-900">
          Requests
        </h2>
        <p className="text-xs text-slate-500" translate="no">
          <span>{rows === null ? "loading…" : `${pending.length} waiting · from the form on the public site`}</span>
        </p>
      </div>

      {approved && (
        <div key="approved" className="mt-4">
          <Reveal
            title={`Invite code for ${approved.request.email}`}
            detail="Only this address can register with it, for 30 days. There is no email service yet: send it yourself."
            value={approved.code}
            onDismiss={() => setApproved(null)}
          />
        </div>
      )}

      {rows !== null && pending.length === 0 && (
        <p key="none" className="mt-4 text-sm text-slate-500">No requests waiting.</p>
      )}
      <ul className="mt-4 divide-y divide-slate-100">
        {pending.map((r) => (
          <li key={r.id} className="grid gap-3 py-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-start">
            <div className="min-w-0">
              <p className="break-words font-medium text-slate-800">{r.email}</p>
              <p className="text-xs text-slate-500">
                <span>{date(r.created_at)}</span>
                {r.shop && <span key="shop"><span> · </span><span className="break-words">{r.shop}</span></span>}
              </p>
              {r.note && <p key="note" className="mt-1 whitespace-pre-wrap break-words text-sm text-slate-600">{r.note}</p>}
              {r.has_account && (
                <p key="has" className="mt-1 text-xs text-amber-700">This address already has an account.</p>
              )}
            </div>
            <div className="flex flex-wrap gap-2">
              <button type="button" className="btn-primary" disabled={r.has_account} onClick={() => approve(r)}>
                Approve
              </button>
              <Confirm label="Decline" confirmLabel="Decline" tone="danger" onConfirm={() => decline(r)} />
            </div>
          </li>
        ))}
      </ul>

      {decided.length > 0 && (
        <div key="decided" className="mt-3 border-t border-slate-100 pt-3">
          <button
            type="button"
            className="tap text-xs text-slate-500 underline decoration-dotted underline-offset-2"
            onClick={() => setShowDecided((v) => !v)}
          >
            <span><span><span>{showDecided ? "Hide" : "Show"}</span> <span>{decided.length}</span> decided</span></span>
          </button>
          {showDecided && (
            <ul key="list" className="mt-2 space-y-1 text-xs text-slate-500">
              {decided.map((r) => (
                <li key={r.id} className="break-words">
                  <span>{r.email}</span><span> · </span><span>{r.status}</span>
                  <span> · </span><span>{r.decided_at ? date(r.decided_at) : ""}</span>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-2 text-xs text-slate-400">Decided requests are deleted after 90 days, undecided ones after 180.</p>
        </div>
      )}
    </section>
  );
}
