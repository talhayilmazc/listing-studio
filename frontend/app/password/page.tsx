"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import { AuthShell, Field, FormError } from "@/components/AuthShell";
import { useSession } from "@/components/SessionProvider";

const MIN_PASSWORD = 12;

/**
 * Change password. Also the screen an admin-issued temporary password lands on,
 * since the server refuses everything else until it is replaced (A4).
 */
export default function PasswordPage() {
  const router = useRouter();
  const { account, loading, setAccount } = useSession();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const tooShort = newPassword.length > 0 && newPassword.length < MIN_PASSWORD;
  const forced = account?.must_change_password ?? false;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setAccount(await api.changePassword(currentPassword, newPassword));
      router.replace("/dashboard");
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Could not reach the server. Please try again.",
      );
      setBusy(false);
    }
  }

  if (loading) return null;

  return (
    <AuthShell
      title={forced ? "Choose a password" : "Change your password"}
      subtitle={
        forced
          ? "You signed in with a temporary password. Pick your own to continue."
          : "Changing your password signs you out everywhere else."
      }
    >
      <form onSubmit={onSubmit} className="space-y-4">
        <Field id="current" label={forced ? "Temporary password" : "Current password"}>
          <input
            id="current"
            type="password"
            className="field"
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
            autoComplete="current-password"
            required
            autoFocus
          />
        </Field>

        <Field id="new" label="New password" hint={`At least ${MIN_PASSWORD} characters.`}>
          <div className="relative">
            <input
              id="new"
              type="password"
              className={
                "field " +
                (tooShort ? "border-amber-400 focus:border-amber-500 focus:ring-amber-500" : "")
              }
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              autoComplete="new-password"
              required
            />
            {newPassword.length > 0 && (
              <span className={"counter " + (tooShort ? "text-amber-700" : "text-slate-400")}>
                {newPassword.length} / {MIN_PASSWORD}
              </span>
            )}
          </div>
        </Field>

        <FormError message={error} />

        <button type="submit" className="btn-primary w-full" disabled={busy || tooShort}>
          {busy ? "Saving…" : "Save password"}
        </button>
      </form>
    </AuthShell>
  );
}
