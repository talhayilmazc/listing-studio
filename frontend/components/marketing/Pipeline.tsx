"use client";

import { useEffect, useRef, useState } from "react";
import { Mockup } from "./Mockup";

/**
 * How it works, as a walk down the page: four stages, and beside them one panel
 * of the product that changes as each stage is reached. On a wide screen the
 * panel stays in view while the text scrolls; on a phone each stage carries its
 * own panel. The text is all there from the start, so nothing waits on motion.
 */

const STAGES = [
  {
    key: "upload",
    title: "Drop your folder",
    body: "Drop in the folder your mockups are already in. Each design's images stay together as one listing. Listyro resizes them, puts them in order and picks the cover.",
    points: ["Front, back and close-up shots stay together", "The SKU is read from your file names", "Change the order or the cover if you like"],
  },
  {
    key: "generate",
    title: "Listyro fills in everything",
    body: "Choose one of your existing listings as the model. Every new listing copies its category, prices, variations, size charts, shipping and section, and gets its own title, 13 tags and description written from the design.",
    points: ["Each shop uses its own model listings", "Written to match what shoppers search for; no ranking promises", "Optional filter for brand and character names"],
  },
  {
    key: "review",
    title: "Take a quick look",
    body: "Every listing arrives complete and easy to read. Change anything you want, or approve it as it is with one click. Anything that might be a problem is flagged, with the reason.",
    points: ["Edit any title, tag or photo", "Flags tell you exactly what to check", "Approve one listing or a whole set you have read"],
  },
  {
    key: "publish",
    title: "Go live now or on a schedule",
    body: "Approved listings go to your shop. Publish with one click, or pick a time and they go live then, in your own time zone. Send the same design to several of your shops at once.",
    points: ["Line up a whole drop for the week", "Each shop gets its own settings", "If Etsy turns something down, you see why and retry in one click"],
  },
] as const;

export function Pipeline() {
  const [active, setActive] = useState(0);
  const items = useRef<(HTMLLIElement | null)[]>([]);

  useEffect(() => {
    if (!("IntersectionObserver" in window)) return;
    // The stage crossing the middle of the screen is the one being read.
    const watch = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) setActive(Number((e.target as HTMLElement).dataset.index));
        }
      },
      { rootMargin: "-45% 0px -45% 0px" },
    );
    items.current.forEach((el) => el && watch.observe(el));
    return () => watch.disconnect();
  }, []);

  return (
    <div className="grid grid-cols-1 gap-10 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)] lg:gap-16">
      {/* Wide screens: one panel that follows the reading. */}
      <div className="hidden lg:block">
        <div className="sticky top-[16vh]">
          <div className="relative aspect-[5/4]">
            {STAGES.map((s, i) => (
              <div
                key={s.key}
                aria-hidden={active !== i}
                className={
                  "absolute inset-0 transition-[opacity,transform] duration-500 ease-out " +
                  (active === i ? "translate-y-0 opacity-100" : "pointer-events-none translate-y-2 opacity-0")
                }
              >
                <Panel stage={s.key} />
              </div>
            ))}
          </div>
          <ol className="mt-5 flex gap-1.5" aria-hidden>
            {STAGES.map((s, i) => (
              <li
                key={s.key}
                className={"h-0.5 flex-1 rounded-full transition-colors duration-500 " + (i <= active ? "bg-brand-600" : "bg-slate-200")}
              />
            ))}
          </ol>
        </div>
      </div>

      <ol className="min-w-0 space-y-16 lg:space-y-0">
        {STAGES.map((s, i) => (
          <li
            key={s.key}
            data-index={i}
            ref={(el) => {
              items.current[i] = el;
            }}
            className={
              "min-w-0 lg:flex lg:min-h-[70vh] lg:flex-col lg:justify-center lg:transition-opacity lg:duration-500 " +
              (active === i ? "lg:opacity-100" : "lg:opacity-40")
            }
          >
            <p className="font-display text-4xl leading-none text-brand-500" aria-hidden translate="no">
              {String(i + 1).padStart(2, "0")}
            </p>
            <h3 className="mt-3 font-display text-3xl leading-tight text-slate-900 sm:text-4xl">{s.title}</h3>
            <p className="mt-4 max-w-[34rem] text-[1.0625rem] leading-relaxed text-slate-600">{s.body}</p>
            <ul className="mt-5 max-w-[34rem] space-y-2 text-sm text-slate-600">
              {s.points.map((p) => (
                <li key={p} className="flex gap-2.5">
                  <span aria-hidden className="mt-[0.6rem] h-1 w-1 shrink-0 rounded-full bg-brand-600" />
                  <span>{p}</span>
                </li>
              ))}
            </ul>
            <div className="mt-7 lg:hidden">
              <Panel stage={s.key} />
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}

function Frame({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex h-full flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-[0_1px_2px_rgba(0,0,0,.04),0_24px_60px_-32px_rgba(28,25,23,.25)]">
      <div className="flex items-center justify-between border-b border-slate-200 bg-stone-50 px-4 py-2.5">
        <span className="font-display text-lg leading-none text-slate-800">{label}</span>
        <span className="rounded-full border border-slate-200 bg-white px-2 py-0.5 text-[11px] font-medium text-slate-500">Sample data</span>
      </div>
      <div className="flex-1 p-4 sm:p-5">{children}</div>
    </div>
  );
}

const Row = ({ k, v }: { k: string; v: string }) => (
  <li className="flex items-center justify-between gap-3 border-b border-slate-100 py-1.5 last:border-0">
    <span className="text-slate-500">{k}</span>
    <span className="flex items-center gap-1.5 text-right font-medium text-slate-800">
      <span>{v}</span>
      <svg aria-hidden viewBox="0 0 16 16" className="h-3.5 w-3.5 text-emerald-600" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M3.5 8.5l3 3 6-7" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </span>
  </li>
);

function Panel({ stage }: { stage: (typeof STAGES)[number]["key"] }) {
  if (stage === "upload") {
    return (
      <Frame label="Uploads">
        <div className="rounded-xl border border-dashed border-slate-300 bg-stone-50 px-4 py-4 text-center text-sm text-slate-500">
          <span className="font-medium text-slate-700">summer-drop/</span>
          <span> · 15 files in 6 folders</span>
        </div>
        <ul className="mt-3 space-y-2 text-sm">
          {(
            [
              ["BR5229", "mountain", ["front.jpg", "back.jpg", "detail.jpg"]],
              ["BR5230", "heart", ["front.jpg", "lifestyle.jpg"]],
              ["BR5231", "paw", ["front.jpg", "back.jpg", "detail.jpg"]],
            ] as const
          ).map(([sku, art, files]) => (
            <li key={sku} className="flex items-center gap-3 rounded-lg border border-slate-200 p-2">
              <Mockup art={art} label="" className="h-11 w-11 shrink-0 rounded-md border border-slate-200" />
              <div className="min-w-0 flex-1">
                <p className="font-medium text-slate-800" translate="no"><span>SKU <span>{sku}</span></span></p>
                <p className="truncate text-xs text-slate-500" translate="no">{files.join(" · ")}</p>
              </div>
              <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700">
                <span><span>{files.length}</span> images</span>
              </span>
            </li>
          ))}
        </ul>
      </Frame>
    );
  }
  if (stage === "generate") {
    return (
      <Frame label="Profile · Standard Tee">
        <p className="label">Copied from your listing</p>
        <ul className="text-sm">
          <Row k="Category" v="T-shirts" />
          <Row k="Variations" v="6 colours × S–3XL" />
          <Row k="Prices" v="per size" />
          <Row k="Size chart" v="image 3, kept" />
          <Row k="Shop section" v="Graphic Tees" />
        </ul>
        <p className="label mt-4">Written from the design</p>
        <div className="rounded-lg border border-slate-200 p-3">
          <p className="text-sm font-medium text-slate-800">Retro Camping Shirt, Mountain Sunset, Vintage Outdoor Style</p>
          <div className="mt-2 flex flex-wrap gap-1">
            {["hiking gift", "camp crew tee", "national park trip", "outdoor dad gift"].map((t) => (
              <span key={t} className="rounded-full border border-slate-200 px-2 py-0.5 text-xs text-slate-500">{t}</span>
            ))}
            <span className="px-1 py-0.5 text-xs text-slate-500">+9</span>
          </div>
        </div>
      </Frame>
    );
  }
  if (stage === "review") {
    return (
      <Frame label="Review">
        <div className="grid grid-cols-[5.5rem_minmax(0,1fr)] gap-4">
          <Mockup art="heart" label="Sample mockup BR5230" className="aspect-[4/5] w-full rounded-lg border border-slate-200" />
          <div className="min-w-0 space-y-3">
            <div>
              <div className="flex items-center justify-between">
                <span className="label mb-0">Title</span>
                <span className="text-xs tabular-nums text-slate-500" translate="no">53 / 100</span>
              </div>
              <p className="mt-1 rounded-lg border border-slate-300 px-2.5 py-1.5 text-sm text-slate-900">
                Funny Nurse Shirt, Flu Season Humor, Hand Washing Joke
              </p>
            </div>
            <div>
              <div className="flex items-center justify-between">
                <span className="label mb-0">Tags</span>
                <span className="text-xs font-medium text-emerald-700" translate="no">13 / 13</span>
              </div>
              <div className="mt-1 flex flex-wrap gap-1">
                {["nurses week", "er nurse gift", "medical humor", "rn graduation"].map((t) => (
                  <span key={t} className="rounded-full border border-slate-200 px-2 py-0.5 text-xs text-slate-600">{t}</span>
                ))}
              </div>
            </div>
          </div>
        </div>
        <p className="mt-4 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-800">
          Compliance check: no brand or character names, no repeated tags.
        </p>
        <div className="mt-3 flex items-center justify-between border-t border-slate-100 pt-3">
          <span className="flex items-center gap-2 text-sm font-medium text-emerald-700">
            <span aria-hidden className="flex h-4 w-4 items-center justify-center rounded-[4px] bg-brand-600 text-[10px] text-white">✓</span>
            <span>Approved</span>
          </span>
          <span className="btn-primary pointer-events-none px-3 py-1.5 text-xs">Create draft</span>
        </div>
      </Frame>
    );
  }
  return (
    <Frame label="Publish">
      <ul className="space-y-2 text-sm">
        {(
          [
            ["BR5229", "mountain", "Draft in your shop", "border-slate-200 bg-slate-50 text-slate-600"],
            ["BR5230", "heart", "Live · you published", "border-emerald-200 bg-emerald-50 text-emerald-700"],
            ["BR5231", "paw", "Scheduled · Fri 5:00 PM CDT", "border-brand-100 bg-brand-50 text-brand-700"],
          ] as const
        ).map(([sku, art, state, look]) => (
          <li key={sku} className="flex items-center gap-3 rounded-lg border border-slate-200 p-2">
            <Mockup art={art} label="" className="h-11 w-11 shrink-0 rounded-md border border-slate-200" />
            <span className="min-w-0 flex-1 truncate font-medium text-slate-800" translate="no">{sku}</span>
            <span className={`shrink-0 rounded-full border px-2 py-0.5 text-xs font-medium ${look}`}>{state}</span>
          </li>
        ))}
      </ul>
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <span className="btn-primary pointer-events-none px-3 py-1.5 text-xs">Publish now</span>
        <span className="btn-secondary pointer-events-none px-3 py-1.5 text-xs">Schedule…</span>
      </div>
      <p className="mt-4 border-t border-slate-100 pt-3 text-xs leading-relaxed text-slate-500">
        A draft is created first; it is never published automatically. Going live takes your click, or a time you set for that draft.
      </p>
    </Frame>
  );
}
