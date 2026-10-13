"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { AuthShell, Field, FormError } from "@/components/AuthShell";
import { useSession } from "@/components/SessionProvider";

export default function LoginPage() {
  const router = useRouter();
  const { setAccount } = useSession();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Arriving from Settings → Delete account: what was done, said once.
  const [deleted, setDeleted] = useState<string | null>(null);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("deleted") !== "1") return;
    const shops = Number(params.get("shops") ?? 0);
    const cancelled = Number(params.get("cancelled") ?? 0);
    setDeleted(
      "Your account is being deleted. " +
        (shops ? `${shops} shop${shops === 1 ? " was" : "s were"} disconnected and the Etsy data we held deleted. ` : "") +
        (cancelled ? `${cancelled} queued or scheduled item${cancelled === 1 ? " was" : "s were"} cancelled. ` : "") +
        "Your uploads, listings and the account itself follow within 30 days at the latest. Nothing on Etsy was changed.",
    );
  }, []);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const account = await api.login(email, password);
      setAccount(account);
      // A temporary password may only be used to replace itself (A4).
      router.replace(account.must_change_password ? "/password" : account.terms_due ? "/accept-terms" : "/dashboard");
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Could not reach the server. Please try again.",
      );
      setBusy(false);
    }
  }

  return (
    <AuthShell
      title="Sign in"
      subtitle="Prepare compliant draft listings from your own original designs."
      footer={
        <>
          Have an invite code?{" "}
          <Link href="/register" className="font-medium text-brand-700 hover:underline">
            Create an account
          </Link>
        </>
      }
    >
      <form onSubmit={onSubmit} className="space-y-4">
        {deleted && (
          <p key="deleted" role="status" className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800" data-testid="account-deleted" translate="no">
            {deleted}
          </p>
        )}
        <Field id="email" label="Email">
          <input
            id="email"
            type="email"
            className="field"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="email"
            required
            autoFocus
          />
        </Field>

        <Field id="password" label="Password">
          <input
            id="password"
            type="password"
            className="field"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </Field>

        <FormError message={error} />

        <button type="submit" className="btn-primary w-full" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>

        <p className="text-xs text-slate-400">
          Forgotten your password? There is no email service during early access — ask for a
          temporary one and you will be prompted to change it when you sign in.
        </p>
      </form>
    </AuthShell>
  );
}
