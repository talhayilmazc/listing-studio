"use client";

import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { useShops } from "@/components/ShopProvider";
import { StatusChip } from "@/components/StatusChip";
import { afterSample, areaBox, NOTHING_SENT, rememberSampleDone, sampleImage, SAMPLE_STEPS, WRITTEN_AHEAD, type Sample } from "@/lib/firstRunPath";
import { checkTags, parseTags } from "@/lib/freeTools/tagCheck";
import { checkTitle } from "@/lib/freeTools/titleCheck";
import { KIND_WORDS, type Line, VERDICT_WORDS } from "@/lib/freeTools/check";

const TONE = { idle: "neutral", pass: "done", fail: "failed", warn: "attention", note: "neutral" } as const;

/**
 * A sample design and a sample mockup template, walked through to a finished draft preview. Nothing here writes
 * anything: no row, no job, no model, no Etsy. The sample never goes to a shop; the seller's own design does, with
 * their approval.
 */
export default function FirstRunPage() {
  const { shops } = useShops();
  const [sample, setSample] = useState<Sample | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [step, setStep] = useState(0);
  const [title, setTitle] = useState("");
  const [tagText, setTagText] = useState("");

  useEffect(() => {
    api.firstRun().then((out) => { setSample(out); setTitle(out.title); setTagText(out.tags.join(", ")); })
      .catch((e) => setError(e instanceof Error ? e.message : "This page could not be read."));
  }, []);
  useEffect(() => { if (step === SAMPLE_STEPS.length - 1) rememberSampleDone(); }, [step]);
  useEffect(() => { window.scrollTo(0, 0); }, [step]);

  const tags = useMemo(() => parseTags(tagText), [tagText]);
  const current = SAMPLE_STEPS[step];
  const after = afterSample(shops?.length ?? 0);

  return (
    <div className="space-y-4" data-testid="first-run">
      <p className="text-sm text-slate-600">From a design to a finished draft, with a sample. Nothing goes to Etsy.</p>
      {error && <p key="p-41-6" role="alert" className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800"><span translate="no">{error}</span></p>}

      <ol className="flex flex-wrap gap-2" data-testid="first-run-steps">
        {SAMPLE_STEPS.map((s, n) => (
          <li key={s.key} aria-current={n === step ? "step" : undefined}
            className={"rounded-full border px-3 py-1 text-xs " + (n === step ? "border-brand-700 bg-brand-50 font-semibold text-brand-900" : n < step ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-slate-200 text-slate-600")}>
            <span translate="no">{n < step ? "✓ " : `${n + 1}. `}</span><span>{s.title}</span>
          </li>
        ))}
      </ol>

      {sample && (
        <section key="section-52-6" className="card space-y-3 p-4" data-testid={`first-run-${current.key}`}>
          {current.key === "design" && (
            <>
              <h2 className="font-semibold text-slate-900">Your design goes in first</h2>
              <p className="text-sm text-slate-700">This one is a sample we drew. With your own, you upload a file, a folder or a ZIP.</p>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={sampleImage("design")} alt="The sample design: a sun over two mountains" loading="lazy" width={320} height={320}
                className="h-auto w-full max-w-xs rounded-md border border-slate-200 bg-[repeating-conic-gradient(#f1f1f1_0_25%,#fff_0_50%)] bg-[length:24px_24px]" />
            </>
          )}
          {current.key === "template" && (
            <>
              <h2 className="font-semibold text-slate-900">A mockup template says where it prints</h2>
              <p className="text-sm text-slate-700">A template is your photo of a blank shirt, with a print area. You check each photo once, and the template keeps it.</p>
              <div className="relative w-full max-w-sm">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={sampleImage("blank")} alt="A blank white shirt" loading="lazy" width={1000} height={1200} className="h-auto w-full rounded-md border border-slate-200" />
                <div data-testid="first-run-area" className="absolute rounded-sm border-2 border-dashed border-brand-700 bg-brand-100/30" style={areaBox(sample.area)} />
              </div>
            </>
          )}
          {current.key === "mockups" && (
            <>
              <h2 className="font-semibold text-slate-900">Mockups, made from your photos</h2>
              <p className="text-sm text-slate-700">One design on each photo. The cloth&apos;s light and folds stay on the print. A dark shirt gets the light version of this design.</p>
              <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
                {sample.colours.map((colour) => (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img key={colour} src={sampleImage(`mockup-${colour}`)} alt={`The design on a ${colour} shirt`} loading="lazy" width={1000} height={1200} className="h-auto w-full rounded-md border border-slate-200" data-testid="first-run-mockup" />
                ))}
              </div>
            </>
          )}
          {current.key === "text" && (
            <>
              <h2 className="font-semibold text-slate-900">Title and tags, against the rules</h2>
              <p className="text-sm text-slate-700"><span translate="no">{WRITTEN_AHEAD}</span> Change something and watch the checks.</p>
              <label className="block text-sm"><span className="label block">Title</span>
                <input className="field w-full" value={title} onChange={(e) => setTitle(e.target.value)} data-testid="first-run-title" /></label>
              <Lines lines={checkTitle(title)} />
              <label className="block text-sm"><span className="label block">Tags, separated by commas</span>
                <textarea className="field w-full" rows={3} value={tagText} onChange={(e) => setTagText(e.target.value)} data-testid="first-run-tags" /></label>
              <Lines lines={checkTags(tags, title)} />
            </>
          )}
          {current.key === "draft" && (
            <>
              <h2 className="font-semibold text-slate-900">Your draft, finished</h2>
              <p role="status" className="rounded-md border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-900" data-testid="first-run-nothing-sent"><span translate="no">{NOTHING_SENT}</span></p>
              <div className="grid gap-4 lg:grid-cols-2" data-testid="first-run-preview">
                <div className="grid grid-cols-4 gap-1 self-start">
                  {sample.colours.map((colour, n) => (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img key={colour} src={sampleImage(`mockup-${colour}`)} alt={n === 0 ? "Cover photo" : `Photo ${n + 1}`} loading="lazy" width={1000} height={1200}
                      className={"h-auto w-full rounded border border-slate-200 " + (n === 0 ? "col-span-4" : "")} />
                  ))}
                </div>
                <div className="min-w-0 space-y-3 text-sm">
                  <div>
                    <p className="break-words text-lg font-semibold text-slate-900" translate="no">{title}</p>
                    <p className="text-slate-700" translate="no"><span><span>{sample.price}</span> <span>{sample.currency}</span> · Section: <span>{sample.section}</span></span></p>
                  </div>
                  <div>
                    <p className="label"><span translate="no"><span>Tags (<span>{tags.length}</span>)</span></span></p>
                    <p className="mt-1 flex flex-wrap gap-1">{tags.map((tag) => <span key={tag} className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-700" translate="no">{tag}</span>)}</p>
                  </div>
                  <div>
                    <p className="label">Description</p>
                    <p className="mt-1 whitespace-pre-line break-words text-slate-700" translate="no">{sample.description}</p>
                  </div>
                  <dl className="grid grid-cols-2 gap-x-3 gap-y-1">
                    <dt className="text-slate-600">Materials</dt><dd className="text-slate-800" translate="no">{sample.materials.join(", ")}</dd>
                    {sample.attributes.map((a) => [<dt key={`t-${a.name}`} className="text-slate-600" translate="no">{a.name}</dt>, <dd key={`d-${a.name}`} className="text-slate-800" translate="no">{a.value}</dd>])}
                  </dl>
                </div>
              </div>
              <p className="text-sm text-slate-700"><span translate="no">{after.line}</span></p>
              <a href={after.href} className="btn-primary inline-flex" data-testid="first-run-next"><span translate="no">{after.label}</span></a>
            </>
          )}
          <div className="flex flex-wrap gap-2 border-t border-slate-200 pt-3">
            {step > 0 && <button key="button-134-12" type="button" className="btn-secondary" onClick={() => setStep(step - 1)}>Back</button>}
            {current.next && <button key="button-135-12" type="button" className="btn-primary" onClick={() => setStep(step + 1)} data-testid="first-run-continue">{current.next}</button>}
          </div>
        </section>
      )}
    </div>
  );
}

function Lines({ lines }: { lines: Line[] }) {
  const shown = lines.filter((line) => line.verdict === "fail" || line.verdict === "warn");
  if (!shown.length) return <p className="text-sm text-emerald-800" data-testid="first-run-checks-ok">Every check is met.</p>;
  return (
    <ul className="space-y-1 text-sm" data-testid="first-run-checks">
      {shown.map((line) => (
        <li key={line.id} className="flex flex-wrap items-center gap-2">
          <StatusChip tone={TONE[line.verdict]}>{VERDICT_WORDS[line.verdict]}</StatusChip>
          <span className="font-medium text-slate-800">{line.name}</span>
          <span className="text-xs text-slate-600">{KIND_WORDS[line.kind]}</span>
          <span className="w-full break-words text-slate-700" translate="no">{line.detail}</span>
        </li>
      ))}
    </ul>
  );
}
