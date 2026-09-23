"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import { AuthShell, Field, FormError } from "@/components/AuthShell";
import { useSession } from "@/components/SessionProvider";

const MIN_PASSWORD = 12;

export default function RegisterPage() {
  const router = useRouter();
  const { setAccount } = useSession();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [accepted, setAccepted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const tooShort = password.length > 0 && password.length < MIN_PASSWORD;
  const canSubmit =
    accepted && !busy && email.trim() !== "" && inviteCode.trim() !== "" && !tooShort;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const account = await api.register(email, password, inviteCode);
      setAccount(account);
      router.replace("/dashboard");
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
      betaNotice
      title="Create your account"
      subtitle="Registration is by invitation during the beta."
      footer={
        <>
          Already have an account?{" "}
          <Link href="/login" className="font-medium text-brand-700 hover:underline">
            Sign in
          </Link>
        </>
      }
    >
      <form onSubmit={onSubmit} className="space-y-4">
        <Field id="invite" label="Invite code" hint="Single use, given to you directly.">
          <input
            id="invite"
            className="field font-mono"
            value={inviteCode}
            onChange={(e) => setInviteCode(e.target.value)}
            required
            autoFocus
          />
        </Field>

        <Field id="email" label="Email">
          <input
            id="email"
            type="email"
            className="field"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="email"
            required
          />
        </Field>

        <Field
          id="password"
          label="Password"
          hint={`At least ${MIN_PASSWORD} characters. A short phrase works well.`}
        >
          <div className="relative">
            <input
              id="password"
              type="password"
              className={
                "field " +
                (tooShort ? "border-amber-400 focus:border-amber-500 focus:ring-amber-500" : "")
              }
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
              required
            />
            {password.length > 0 && (
              <span
                className={"counter " + (tooShort ? "text-amber-700" : "text-slate-400")}
                title={`${MIN_PASSWORD} characters minimum`}
              >
                {password.length} / {MIN_PASSWORD}
              </span>
            )}
          </div>
        </Field>

        {/* ToU: terms and privacy must be accepted by an explicit click. */}
        <label className="flex cursor-pointer items-start gap-2.5 text-sm text-slate-600">
          <input
            type="checkbox"
            checked={accepted}
            onChange={(e) => setAccepted(e.target.checked)}
            className="mt-0.5 h-4 w-4 shrink-0 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
          />
          <span>
            I agree to the{" "}
            <Link href="/terms" className="font-medium text-brand-700 hover:underline">
              Terms of Service
            </Link>{" "}
            and{" "}
            <Link href="/privacy" className="font-medium text-brand-700 hover:underline">
              Privacy Policy
            </Link>
            , including that my design images are sent to Anthropic for analysis.
          </span>
        </label>

        <FormError message={error} />

        <button type="submit" className="btn-primary w-full" disabled={!canSubmit}>
          {busy ? "Creating account…" : "Create account"}
        </button>
      </form>
    </AuthShell>
  );
}
