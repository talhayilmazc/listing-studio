"use client";

import Link from "next/link";
import { useState } from "react";

/**
 * The request goes to the admin's pending list; an approved one becomes an
 * invite code for that address. The "website" field is hidden from people and
 * left empty by them; anything typed into it marks the submission as a bot's.
 */
export function InviteForm() {
  const [email, setEmail] = useState("");
  const [shop, setShop] = useState("");
  const [shopCount, setShopCount] = useState("1");
  const [note, setNote] = useState("");
  const [website, setWebsite] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "sent">("idle");
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setState("sending");
    setError(null);
    try {
      const res = await fetch("/api/invite-requests", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, shop, shop_count: Number(shopCount), note, website }),
      });
      if (res.status === 202) {
        setState("sent");
        return;
      }
      const body = await res.json().catch(() => null);
      const detail = body?.detail;
      setError(
        typeof detail === "string"
          ? detail
          : res.status === 422
            ? "Please check the email address, your shop's name or link and the number of shops, and keep the note under 1,000 characters."
            : "Something went wrong. Please try again in a moment.",
      );
      setState("idle");
    } catch {
      setError("We could not reach the server. Please check your connection and try again.");
      setState("idle");
    }
  }

  if (state === "sent") {
    return (
      <div role="status" className="card p-6 sm:p-8">
        <h2 className="font-display text-3xl text-slate-900">Thank you.</h2>
        <p className="mt-3 text-[1.0625rem] leading-relaxed text-slate-600">
          We read every request ourselves. When a spot opens, we will email an invite to{" "}
          <span className="font-medium text-slate-900">{email.trim()}</span>, and it will work only for that address.
        </p>
        <p className="mt-6 text-sm text-slate-500">
          <Link href="/" className="font-medium text-brand-700 underline decoration-brand-100 underline-offset-4">
            Back to the home page
          </Link>
        </p>
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="card space-y-5 p-6 sm:p-8" noValidate={false}>
      <p className="rounded-lg border border-slate-200 bg-stone-50 px-3 py-2.5 text-sm leading-relaxed text-slate-700" data-testid="invite-copy">
        Listyro is invite-only while we onboard sellers in small groups. We&apos;ll email you when a spot opens.
      </p>
      <div>
        <label htmlFor="ir-email" className="label">Email</label>
        <input
          id="ir-email"
          type="email"
          required
          autoComplete="email"
          maxLength={254}
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="field"
        />
        <p className="mt-1 text-xs text-slate-500">The invite code is sent here and works only for this address.</p>
      </div>
      <div>
        <label htmlFor="ir-shop" className="label">Shop name or link</label>
        <input
          id="ir-shop"
          type="text"
          required
          maxLength={200}
          autoComplete="organization"
          value={shop}
          onChange={(e) => setShop(e.target.value)}
          className="field"
        />
      </div>
      <div>
        <label htmlFor="ir-count" className="label">Number of shops</label>
        <input
          id="ir-count"
          type="number"
          inputMode="numeric"
          required
          min={1}
          max={500}
          value={shopCount}
          onChange={(e) => setShopCount(e.target.value)}
          className="field w-32"
        />
        <p className="mt-1 text-xs text-slate-500">How many Etsy shops you run yourself.</p>
      </div>
      <div>
        <label htmlFor="ir-note" className="label">A short note <span className="normal-case tracking-normal text-slate-500">(optional)</span></label>
        <textarea
          id="ir-note"
          rows={4}
          maxLength={1000}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="What you sell, and how many new designs you add in a month"
          className="field"
        />
      </div>
      {/* For bots only: hidden from people and from screen readers. */}
      <div aria-hidden="true" className="absolute -left-[9999px] h-px w-px overflow-hidden">
        <label htmlFor="ir-website">Website</label>
        <input id="ir-website" type="text" tabIndex={-1} autoComplete="off" value={website} onChange={(e) => setWebsite(e.target.value)} />
      </div>
      {error && (
        <p key="error" role="alert" className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
          {error}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-4">
        <button type="submit" className="btn-primary px-5 py-3 text-[0.9375rem]"
          disabled={state === "sending" || !email.trim() || !shop.trim() || !(Number(shopCount) >= 1)}>
          {state === "sending" ? "Sending…" : "Request an invite"}
        </button>
        <p className="text-xs text-slate-500">
          <span>We use this only to decide on your request, and delete it after 90 days. </span>
          <Link href="/privacy#collect" className="underline decoration-slate-300 underline-offset-2 hover:text-slate-800">
            Privacy Policy
          </Link>
        </p>
      </div>
    </form>
  );
}
