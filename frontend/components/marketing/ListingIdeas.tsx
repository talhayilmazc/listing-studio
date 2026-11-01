"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/** Asks the server what it can do today, shows Cloudflare's check, and shows what comes back. */

type Status = { available: boolean; reason: string | null; message: string | null; site_key: string | null; remaining: number; per_day: number };
type Ideas = { titles: string[]; tags: string[]; remaining: number };
type Turnstile = {
  render: (el: HTMLElement, options: { sitekey: string; callback: (token: string) => void; "expired-callback": () => void; "error-callback": () => void }) => string;
  reset: (id: string) => void;
};

declare global {
  interface Window { turnstile?: Turnstile }
}

const SCRIPT = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
const MIN = 10;
const MAX = 300;

function loadTurnstile(): Promise<Turnstile> {
  return new Promise((resolve, reject) => {
    if (window.turnstile) return resolve(window.turnstile);
    const script = document.createElement("script");
    script.src = SCRIPT;
    script.async = true;
    script.onload = () => (window.turnstile ? resolve(window.turnstile) : reject(new Error("no turnstile")));
    script.onerror = () => reject(new Error("no turnstile"));
    document.head.appendChild(script);
  });
}

export function ListingIdeas() {
  const [status, setStatus] = useState<Status | null>(null);
  const [text, setText] = useState("");
  const [token, setToken] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [ideas, setIdeas] = useState<Ideas | null>(null);
  const [error, setError] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const widget = useRef<{ api: Turnstile; id: string } | null>(null);

  useEffect(() => {
    fetch("/api/free-tools/ideas")
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(String(res.status)))))
      .then(setStatus)
      .catch(() => setError("We could not reach the server. Please check your connection and try again."));
  }, []);

  const siteKey = status?.available ? status.site_key : null;
  useEffect(() => {
    if (!siteKey || widget.current || !box.current) return;
    const el = box.current;
    loadTurnstile()
      .then((api) => {
        const id = api.render(el, { sitekey: siteKey, callback: setToken, "expired-callback": () => setToken(null), "error-callback": () => setToken(null) });
        widget.current = { api, id };
      })
      .catch(() => setError("Cloudflare's check would not load. Please reload the page, or try without a content blocker."));
  }, [siteKey]);

  const submit = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    if (!token) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/free-tools/ideas", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ description: text.trim(), turnstile_token: token }),
      });
      const body = await res.json().catch(() => null);
      if (res.ok) {
        setIdeas(body);
        setStatus((s) => (s ? { ...s, remaining: body.remaining } : s));
      } else {
        setError(typeof body?.detail === "string" ? body.detail : "We could not write ideas just now. Please try again in a little while.");
      }
    } catch {
      setError("We could not reach the server. Please check your connection and try again.");
    } finally {
      setBusy(false);
      setToken(null);
      if (widget.current) widget.current.api.reset(widget.current.id);
    }
  }, [text, token]);

  const size = text.trim().length;
  const fits = size >= MIN && size <= MAX;
  const closed = status !== null && !status.available;
  return (
    <div className="space-y-4">
      <form onSubmit={submit} className="rounded-2xl border border-slate-200 bg-white p-4 sm:p-5" data-testid="ideas-form">
        <label htmlFor="ideas-text" className="label">Describe your item</label>
        <textarea id="ideas-text" rows={3} value={text} onChange={(e) => setText(e.target.value)} className="field text-base" data-testid="ideas-input" disabled={closed} />
        <p className="mt-2 flex flex-wrap justify-between gap-x-4 gap-y-1 text-sm text-slate-600" translate="no">
          <span data-testid="ideas-count">{`${size} of ${MAX} characters`}</span>
          <span data-testid="ideas-remaining">{status?.available ? `${status.remaining} of ${status.per_day} left today` : ""}</span>
        </p>
        <div ref={box} className="mt-3 min-h-[65px]" data-testid="ideas-check" />
        <button type="submit" className="btn-primary mt-3 px-5 py-2.5" disabled={!fits || !token || busy || closed || status?.remaining === 0} data-testid="ideas-submit">
          {busy ? "Writing…" : "Write ideas"}
        </button>
      </form>
      <div role="status" className="min-h-[1.5rem] text-slate-900" translate="no" data-testid="ideas-message">{error ?? (closed ? status?.message ?? "This tool is off for now." : "")}</div>
      {ideas ? (
        <section key="ideas" aria-label="Your ideas" className="rounded-2xl border border-slate-200 bg-white p-4 sm:p-5" translate="no" data-testid="ideas-result">
          <h2 className="font-display text-2xl text-slate-900">Titles</h2>
          <ul className="mt-2 list-disc space-y-1.5 pl-5 text-slate-800">{ideas.titles.map((title) => <li key={title}>{title}</li>)}</ul>
          <h2 className="mt-5 font-display text-2xl text-slate-900">Tags</h2>
          <ul className="mt-2 flex flex-wrap gap-2">{ideas.tags.map((tag) => <li key={tag} className="rounded-lg border border-slate-200 px-2.5 py-1 text-sm text-slate-800">{tag}</li>)}</ul>
          <p className="mt-4 text-sm text-slate-600">
            <span>Check them against your item, then in the </span>
            <a href="/free-tools/title-checker" className="font-medium text-brand-700 underline decoration-brand-100 underline-offset-4">title checker</a>
            <span> and the </span>
            <a href="/free-tools/tag-checker" className="font-medium text-brand-700 underline decoration-brand-100 underline-offset-4">tag checker</a>
            <span>.</span>
          </p>
        </section>
      ) : null}
    </div>
  );
}
