"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { AuthShell, FormError } from "@/components/AuthShell";
import { useSession } from "@/components/SessionProvider";
import { CONSENT_STATEMENT, LAST_UPDATED, PROVIDER_DISCLAIMER } from "@/lib/legal";

/**
 * The one-time acceptance screen. An account that has not agreed to the current
 * Terms of Service and Privacy Policy (an account made before acceptance was
 * recorded, or any account after the version changes) lands here at its next
 * sign-in, and the server refuses everything else until it has agreed.
 */
export default function AcceptTermsPage() {
  const router = useRouter();
  const { account, loading, setAccount, signOut } = useSession();
  const [agreed, setAgreed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Nothing to accept (already agreed, or signed out): on to the app.
  useEffect(() => {
    if (!loading && account && !account.terms_due) router.replace("/dashboard");
  }, [account, loading, router]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!account) return;
    setBusy(true);
    setError(null);
    try {
      setAccount(await api.acceptTerms(account.terms_version ?? ""));
      router.replace("/dashboard");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not reach the server. Please try again.");
      setBusy(false);
    }
  }

  return (
    <AuthShell
      title="Before you go on"
      subtitle={`Our Terms of Service and Privacy Policy were updated on ${LAST_UPDATED}.`}
      footer={
        <button type="button" className="tap font-medium text-slate-600 underline" onClick={signOut}>
          Sign out instead
        </button>
      }
    >
      <form onSubmit={onSubmit} className="space-y-4" data-testid="accept-terms">
        <p className="text-sm leading-relaxed text-slate-600">
          Please read them, then confirm below. They say who provides Listyro, what it reads from your Etsy
          shops and for how long, that nothing is published without you, and how to disconnect a shop or delete
          your account.
        </p>
        <p className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
          <Link href="/terms" target="_blank" className="tap font-medium text-brand-700 underline">
            Terms of Service ↗
          </Link>
          <Link href="/privacy" target="_blank" className="tap font-medium text-brand-700 underline">
            Privacy Policy ↗
          </Link>
        </p>
        <p className="text-[11px] leading-relaxed text-slate-500">{PROVIDER_DISCLAIMER}</p>
        <label className="flex cursor-pointer items-start gap-2.5 text-sm text-slate-700 max-sm:min-h-[2.75rem]">
          <input
            type="checkbox"
            checked={agreed}
            onChange={(e) => setAgreed(e.target.checked)}
            className="mt-0.5 h-4 w-4 shrink-0 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
            data-testid="consent-checkbox"
          />
          <span>{CONSENT_STATEMENT}</span>
        </label>
        <FormError message={error} />
        <button type="submit" className="btn-primary w-full" disabled={!agreed || busy || !account}>
          {busy ? "Saving…" : "Agree and continue"}
        </button>
        <p className="text-xs text-slate-500">
          If you do not agree, you can sign out; to have your account deleted, write to us at the address below.
        </p>
      </form>
    </AuthShell>
  );
}
