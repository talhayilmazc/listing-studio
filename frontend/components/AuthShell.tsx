"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

// Exact ToU notice — must not be altered or abbreviated.
const TRADEMARK_NOTICE =
  "The term 'Etsy' is a trademark of Etsy, Inc. This application uses the Etsy API but is not endorsed or certified by Etsy, Inc.";

/**
 * Frame for the signed-out screens. No rail: there is nothing to navigate to
 * yet, so the page is the serif wordmark over a single card on the warm ground,
 * keeping the redesign's three surfaces (ground, card, accent) intact.
 */
export function AuthShell({
  title,
  subtitle,
  children,
  footer,
  betaNotice = false,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
  /** Shown where someone decides to join (register), not on every sign-in. */
  betaNotice?: boolean;
}) {
  return (
    <div className="flex min-h-screen flex-col bg-stone-50 px-6 py-10">
      <div className="mx-auto flex w-full max-w-[26rem] flex-1 flex-col justify-center">
        <div className="mb-7 flex items-center justify-center gap-2.5">
          <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand-600 text-sm font-bold text-white">
            LS
          </span>
          <span className="font-display text-3xl leading-none text-slate-900">Listing Studio</span>
        </div>

        <div className="card p-7">
          <h1 className="font-display text-2xl leading-tight text-slate-900">{title}</h1>
          {subtitle && <p className="mt-1.5 text-sm text-slate-500">{subtitle}</p>}
          <div className="mt-6">{children}</div>
        </div>

        {betaNotice && <BetaNotice />}
        {footer && <div className="mt-5 text-center text-sm text-slate-500">{footer}</div>}
        <AuthFooter />
      </div>
    </div>
  );
}

/**
 * Beta notice (production-spec E3). Shown on register, before someone commits:
 * these are real sellers with real shops, and they must know what they are
 * joining before they hand over an account. Returning users have seen it.
 */
export function BetaNotice() {
  return (
    <div className="mt-5 rounded-xl border border-amber-200 bg-amber-50 p-4">
      <p className="text-xs font-medium uppercase tracking-[0.08em] text-amber-800">Beta</p>
      <p className="mt-1.5 text-xs leading-relaxed text-amber-900">
        This product is in beta and free to use. It may contain errors, and your listings are
        created as drafts you approve yourself — nothing is ever published to Etsy without your
        explicit action. Please keep your own copies of your design files; we cannot guarantee
        against data loss.
      </p>
    </div>
  );
}

/** Compliance footer: trademark line, policies, support address (ToU). */
function AuthFooter() {
  const [email, setEmail] = useState("support@example.com");

  useEffect(() => {
    api
      .meta()
      .then((m) => setEmail(m.support_email))
      .catch(() => {});
  }, []);

  return (
    <div className="mt-8 space-y-3 text-center text-xs text-slate-400">
      <p className="leading-relaxed">{TRADEMARK_NOTICE}</p>
      <div className="flex flex-wrap items-center justify-center gap-x-4 gap-y-1">
        <Link href="/terms" className="hover:text-slate-600">
          Terms of Service
        </Link>
        <Link href="/privacy" className="hover:text-slate-600">
          Privacy Policy
        </Link>
        <a href={`mailto:${email}`} className="font-medium text-brand-700 hover:underline">
          {email}
        </a>
      </div>
    </div>
  );
}

/** Field with a label and an optional inline hint, matching the app's fields. */
export function Field({
  id,
  label,
  hint,
  children,
}: {
  id: string;
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="label" htmlFor={id}>
        {label}
      </label>
      {children}
      {hint && <p className="mt-1 text-xs text-slate-400">{hint}</p>}
    </div>
  );
}

/** Validation and server errors: thin red text, never a shouting box. */
export function FormError({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <p role="alert" className="text-sm text-rose-700">
      {message}
    </p>
  );
}
