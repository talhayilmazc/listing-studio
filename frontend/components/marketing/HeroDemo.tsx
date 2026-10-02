"use client";

import { useEffect, useRef, useState } from "react";
import { Mockup, type Art } from "./Mockup";

/**
 * The hero shows the product doing its job: a folder of mockups becomes a grid
 * of draft cards, the seller approves some, schedules one. It is built from the
 * app's own surfaces (cards, pills, tag chips), not a video, so it is sharp at
 * any size and costs no download.
 *
 * It plays once when it comes into view and then rests on the finished state;
 * nothing loops beside the text someone is reading. With reduced motion it
 * starts finished. Only opacity and transform change, inside a box whose size
 * is fixed up front, so nothing on the page moves.
 */

const DESIGNS: { sku: string; art: Art; title: string; tags: string[]; files: number }[] = [
  { sku: "BR5229", art: "mountain", title: "Retro Camping Shirt, Mountain Sunset, Vintage Outdoor Style", tags: ["hiking gift", "camp crew tee"], files: 3 },
  { sku: "BR5230", art: "heart", title: "Funny Nurse Shirt, Flu Season Humor, Hand Washing Joke", tags: ["nurses week", "er nurse gift"], files: 2 },
  { sku: "BR5231", art: "paw", title: "Dog Mom Sweatshirt, Paw Print Heart, Rescue Mama", tags: ["dog lover gift", "fur mama tee"], files: 3 },
  { sku: "BR5232", art: "books", title: "Book Lover Shirt, Stacked Books, Reading Teacher", tags: ["librarian gift", "bookish tee"], files: 2 },
  { sku: "BR5233", art: "leaf", title: "Plant Lady Shirt, Botanical Leaves, Gardening Humor", tags: ["plant mom gift", "garden club"], files: 3 },
  { sku: "BR5234", art: "cup", title: "Coffee First Hoodie, Morning Person Humor, Cozy Cafe", tags: ["barista gift", "caffeine tee"], files: 2 },
];

// What each card ends up as: the seller's decision, never the app's.
const OUTCOME = ["approved", "approved", "scheduled", "draft", "draft", "draft"] as const;

// 0 folder · 1 files read · 2 cards appear · 3 listings written · 4 drafts · 5 approved · 6 scheduled
const STEPS = [700, 1300, 1500, 1500, 1400, 1300];
const DONE = STEPS.length;

const CAPTION = [
  "summer-drop/ · 6 designs, 15 mockups",
  "Sorting the mockups into listings",
  "Looking at each design",
  "Writing titles, tags and descriptions",
  "Category, prices and size charts filled in",
  "Approved with one click",
  "Ready to go live, now or on schedule",
];

export function HeroDemo() {
  const [step, setStep] = useState(0);
  const [run, setRun] = useState(0);
  const stage = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setStep(DONE);
      return;
    }
    const timers: number[] = [];
    const play = () => {
      let at = 0;
      STEPS.forEach((wait, i) => {
        at += wait;
        timers.push(window.setTimeout(() => setStep(i + 1), at));
      });
    };
    const el = stage.current;
    if (!el || !("IntersectionObserver" in window)) {
      play();
      return () => timers.forEach(clearTimeout);
    }
    const seen = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          seen.disconnect();
          play();
        }
      },
      { threshold: 0.35 },
    );
    seen.observe(el);
    return () => {
      seen.disconnect();
      timers.forEach(clearTimeout);
    };
  }, [run]);

  const replay = () => {
    setStep(0);
    setRun((n) => n + 1);
  };

  return (
    <div ref={stage} className="relative">
      <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-[0_1px_2px_rgba(0,0,0,.04),0_24px_60px_-28px_rgba(28,25,23,.28)]">
        {/* Window bar: where you are, and what is happening right now. */}
        <div className="flex items-center gap-3 border-b border-slate-200 bg-stone-50 px-4 py-2.5">
          <span aria-hidden className="flex gap-1.5">
            <i className="h-2.5 w-2.5 rounded-full bg-slate-200" />
            <i className="h-2.5 w-2.5 rounded-full bg-slate-200" />
            <i className="h-2.5 w-2.5 rounded-full bg-slate-200" />
          </span>
          <p className="min-w-0 flex-1 truncate text-xs text-slate-500" aria-live="off" translate="no">
            <span key={step} className="mk-caption block truncate">{CAPTION[step]}</span>
          </p>
          <span className="hidden rounded-full border border-slate-200 bg-white px-2 py-0.5 text-[11px] font-medium text-slate-500 sm:inline">
            Sample data
          </span>
        </div>

        <div className="grid grid-cols-1 gap-0 sm:grid-cols-[minmax(0,11.5rem)_minmax(0,1fr)]">
          {/* The folder: what the seller starts with. */}
          <div className="min-w-0 border-b border-slate-200 bg-stone-50/60 p-3 sm:border-b-0 sm:border-r sm:p-4">
            <p className="mb-2 flex items-center gap-1.5 text-xs font-medium text-slate-700">
              <svg aria-hidden viewBox="0 0 20 20" className="h-4 w-4 text-brand-600" fill="currentColor">
                <path d="M2 5.5A1.5 1.5 0 0 1 3.5 4h4l2 2h7A1.5 1.5 0 0 1 18 7.5v7a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 2 14.5z" />
              </svg>
              <span>summer-drop</span>
            </p>
            <ul className="grid grid-cols-2 gap-1.5 sm:grid-cols-1 sm:gap-1">
              {DESIGNS.map((d, i) => (
                <li
                  key={d.sku}
                  className="flex items-center gap-2 rounded-md px-1.5 py-1 text-[11px] text-slate-600 transition-colors duration-500"
                  style={{ transitionDelay: `${i * 90}ms`, backgroundColor: step >= 2 ? "transparent" : undefined }}
                >
                  <Mockup art={d.art} label="" className="h-6 w-6 shrink-0 rounded-[4px] border border-slate-200" />
                  <span className="min-w-0 flex-1 truncate" translate="no">
                    <span className="font-medium text-slate-700">{d.sku}</span>
                    <span className="hidden text-slate-500 sm:inline"><span> · <span>{d.files}</span> files</span></span>
                  </span>
                  <svg
                    aria-hidden
                    viewBox="0 0 16 16"
                    className="h-3.5 w-3.5 shrink-0 text-emerald-600 transition-opacity duration-300"
                    style={{ opacity: step >= 2 ? 1 : 0, transitionDelay: `${i * 110}ms` }}
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                  >
                    <path d="M3.5 8.5l3 3 6-7" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </li>
              ))}
            </ul>
          </div>

          {/* The drafts: what they end up with. Three hidden on phones to keep it readable. */}
          <ul className="grid min-w-0 grid-cols-2 gap-2.5 p-3 sm:gap-3 sm:p-4 lg:grid-cols-3">
            {DESIGNS.map((d, i) => {
              const shown = step >= 2;
              const written = step >= 3;
              const outcome = step >= 6 ? OUTCOME[i] : step >= 5 && OUTCOME[i] === "approved" ? "approved" : "draft";
              return (
                <li
                  key={d.sku}
                  className={
                    "card min-w-0 overflow-hidden transition-[opacity,transform] duration-500 ease-out " +
                    (i >= 4 ? "hidden lg:block " : "") +
                    (shown ? "translate-y-0 opacity-100" : "translate-y-3 opacity-0")
                  }
                  style={{ transitionDelay: shown ? `${i * 110}ms` : "0ms" }}
                >
                  <Mockup art={d.art} label={`Sample mockup ${d.sku}`} className="aspect-[5/4] w-full" />
                  <div className="space-y-1.5 p-2.5">
                    <div className="relative min-h-[2.6rem]">
                      {/* Skeleton and text share one box: writing replaces waiting without a shift. */}
                      <div
                        aria-hidden
                        className="absolute inset-0 space-y-1.5 transition-opacity duration-300"
                        style={{ opacity: written ? 0 : 1 }}
                      >
                        <div className="h-2.5 w-11/12 animate-pulse rounded bg-slate-100" />
                        <div className="h-2.5 w-8/12 animate-pulse rounded bg-slate-100" />
                      </div>
                      <p
                        className="line-clamp-2 text-[12px] font-medium leading-snug text-slate-800 transition-opacity duration-500"
                        style={{ opacity: written ? 1 : 0, transitionDelay: written ? `${i * 140}ms` : "0ms" }}
                      >
                        {d.title}
                      </p>
                    </div>
                    <div
                      className="flex flex-wrap gap-1 transition-opacity duration-500"
                      style={{ opacity: written ? 1 : 0, transitionDelay: written ? `${200 + i * 140}ms` : "0ms" }}
                    >
                      {d.tags.map((t) => (
                        <span key={t} className="rounded-full border border-slate-200 px-1.5 py-px text-[10px] text-slate-500">
                          {t}
                        </span>
                      ))}
                      <span className="rounded-full px-1 py-px text-[10px] text-slate-500">+11</span>
                    </div>
                    <div
                      className="flex items-center justify-between gap-2 border-t border-slate-100 pt-1.5 transition-opacity duration-500"
                      style={{ opacity: step >= 4 ? 1 : 0, transitionDelay: step === 4 ? `${i * 90}ms` : "0ms" }}
                    >
                      <span className="truncate text-[10px] text-slate-500" translate="no">{d.sku}</span>
                      <Pill outcome={outcome} />
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      </div>

      <button
        type="button"
        onClick={replay}
        className={
          "tap absolute -bottom-9 right-1 text-xs text-slate-500 underline decoration-slate-300 decoration-dotted underline-offset-2 transition-opacity duration-500 hover:text-slate-700 " +
          (step >= DONE ? "opacity-100" : "pointer-events-none opacity-0")
        }
      >
        Replay
      </button>
    </div>
  );
}

function Pill({ outcome }: { outcome: "draft" | "approved" | "scheduled" }) {
  const look = {
    draft: "border-amber-200 bg-amber-50 text-amber-700",
    approved: "border-emerald-200 bg-emerald-50 text-emerald-700",
    scheduled: "border-brand-100 bg-brand-50 text-brand-700",
  }[outcome];
  const text = { draft: "Draft · review", approved: "Approved", scheduled: "Fri 5:00 PM" }[outcome];
  return (
    <span
      translate="no"
      className={`inline-flex shrink-0 items-center rounded-full border px-1.5 py-px text-[10px] font-medium transition-colors duration-500 ${look}`}
    >
      {text}
    </span>
  );
}
