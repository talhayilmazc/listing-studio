"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { TOAST_EVENT, drop, push, type Toast } from "@/lib/toast";

/**
 * The app's notes (lib/toast.ts), at the foot of the screen above everything else. Read out by
 * screen readers as they arrive (a polite live region); each closes by itself after its time,
 * on its own button, or once its action ran. No animation: it simply appears.
 */
export function Toaster() {
  const [list, setList] = useState<Toast[]>([]);
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>());

  useEffect(() => {
    const onToast = (e: Event) => {
      const t = (e as CustomEvent<Toast>).detail;
      setList((l) => push(l, t));
      if (t.seconds > 0) {
        timers.current.set(t.id, setTimeout(() => close(t.id), t.seconds * 1000));
      }
    };
    window.addEventListener(TOAST_EVENT, onToast);
    const running = timers.current;
    return () => {
      window.removeEventListener(TOAST_EVENT, onToast);
      running.forEach(clearTimeout);
    };
  }, []);

  function close(id: number) {
    const timer = timers.current.get(id);
    if (timer) clearTimeout(timer);
    timers.current.delete(id);
    setList((l) => drop(l, id));
  }

  return (
    <div
      aria-live="polite"
      role="status"
      className="pointer-events-none fixed inset-x-0 bottom-4 z-[60] flex flex-col items-center gap-2 px-4"
      data-testid="toaster"
    >
      {list.map((t) => (
        <div
          key={t.id}
          data-testid="toast"
          className={
            "pointer-events-auto flex w-full max-w-md items-center gap-3 rounded-[var(--radius-md)] px-4 py-3 text-sm shadow-[var(--shadow-lg)] " +
            (t.tone === "error" ? "bg-[#7f1d1d] text-white" : "bg-[var(--ink)] text-white")
          }
        >
          <span className="min-w-0 flex-1" translate="no">{t.message}</span>
          {t.action?.href ? (
            <Link key="go" href={t.action.href} onClick={() => close(t.id)} className="tap shrink-0 font-semibold text-[var(--tag)] underline-offset-2 hover:underline">
              {t.action.label}
            </Link>
          ) : t.action?.run ? (
            <button
              key="run"
              type="button"
              onClick={async () => {
                close(t.id);
                await t.action?.run?.();
              }}
              className="tap shrink-0 font-semibold text-[var(--tag)] underline-offset-2 hover:underline"
            >
              {t.action.label}
            </button>
          ) : null}
          <button type="button" onClick={() => close(t.id)} aria-label="Close" className="tap shrink-0 text-white/75 hover:text-white">
            <svg aria-hidden width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <path d="M18 6L6 18M6 6l12 12" strokeLinecap="round" />
            </svg>
          </button>
        </div>
      ))}
    </div>
  );
}
