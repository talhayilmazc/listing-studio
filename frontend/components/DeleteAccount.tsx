"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import { RETENTION } from "@/lib/legal";
import { useSession } from "./SessionProvider";

/** What the seller types to confirm: the app cannot bring an account back. */
const WORD = "DELETE";

/**
 * Settings → Delete account. Says exactly what happens before anything does:
 * every shop is disconnected at once (tokens and Etsy data deleted, queued and
 * scheduled work cancelled), then the uploads, the listings and the account
 * itself are deleted by a job. Nothing on Etsy changes.
 */
export function DeleteAccount() {
  const router = useRouter();
  const { setAccount } = useSession();
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [word, setWord] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const done = await api.deleteAccount(password);
      setAccount(null);
      const cancelled = done.queued_jobs + done.scheduled_publishes + done.planned_drafts;
      router.replace(`/login?deleted=1&shops=${done.shops_disconnected}&cancelled=${cancelled}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the server. Please try again.");
      setBusy(false);
    }
  }

  return (
    <section className="card space-y-3 border-rose-100 p-5" data-testid="delete-account">
      <div>
        <h2 className="text-sm font-semibold text-slate-800">Delete account</h2>
        <p className="mt-1 text-sm text-slate-500">
          Deletes your account and everything in it. Your drafts and listings on Etsy are not touched: they
          are yours and stay in your shop.
        </p>
      </div>
      {!open ? (
        <button key="open" type="button" className="btn-danger" onClick={() => setOpen(true)}>
          Delete my account…
        </button>
      ) : (
        <form key="form" onSubmit={onSubmit} className="space-y-3">
          <ul className="list-disc space-y-1 pl-5 text-sm text-slate-600">
            <li>
              <strong>At once:</strong> every shop is disconnected (its tokens and the Etsy data we hold are
              deleted), anything queued or scheduled is cancelled, and you are signed out everywhere.
            </li>
            <li>
              <strong>{`Then, within ${RETENTION.accountDeletionDays} days at the latest`}</strong>{" "}
              (normally within minutes): your uploads, batches, listings, profiles, costs and reports, and
              the account itself are deleted.
            </li>
            <li>
              <span>{`Kept: entries in our action log with counts only and no content, our own AI cost figures, and database backups for ${RETENTION.backupDays} days.`}</span>
            </li>
            <li>This cannot be undone.</li>
          </ul>
          <label className="block">
            <span className="label">Your password</span>
            <input type="password" className="field" value={password} onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password" required />
          </label>
          <label className="block">
            <span className="label">{`Type ${WORD} to confirm`}</span>
            <input className="field font-mono" value={word} onChange={(e) => setWord(e.target.value)} autoComplete="off"
              spellCheck={false} required />
          </label>
          {error && <p key="error" role="alert" className="text-sm text-rose-700">{error}</p>}
          <div className="flex flex-wrap items-center gap-3">
            <button type="submit" className="btn-primary bg-rose-600 hover:bg-rose-700" disabled={busy || !password || word.trim() !== WORD}>
              {busy ? "Deleting…" : "Delete my account"}
            </button>
            <button type="button" className="tap text-sm text-slate-600 underline" onClick={() => setOpen(false)} disabled={busy}>
              Keep my account
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
