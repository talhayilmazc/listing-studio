"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { Help } from "@/components/Help";
import { ShopBadge } from "@/components/ShopPicker";
import { useShops } from "@/components/ShopProvider";
import { StatusChip } from "@/components/StatusChip";
import { useSession } from "@/components/SessionProvider";
import { formatWhen } from "@/lib/schedule";
import {
  approvalSentence, diffOf, FIX_KINDS, FIX_WORDS, isWorking, KIND_WORDS, RULES, rewriteSentence, SAID_FIRST, scoreTone, toLookAt, VERDICT_TONE,
  VERDICT_WORDS, withinCap, type DiffLine, type Fix, type HealthCheck, type HealthListing, type HealthPage, type PreviewAnswer, type RewriteCheck,
} from "@/lib/listingHealth";

import { Txt } from "@/components/Txt";
type Show = "needs_work" | "all";
type RewriteAsk = { ids: number[]; check: RewriteCheck };

const MAX_ASKS_FOR_ATTRIBUTES = 20;
const WAITING = (f: Fix) => f.state === "proposed" || f.state === "writing" || f.state === "could_not_write";

/**
 * The seller's own active listings against Etsy's published rules and our advice. A preview shows each change
 * before and after; only "Approve" writes to a live listing, at most 50 at a time, read back, with Undo for 7 days.
 */
export default function ListingHealthPage() {
  const { selected } = useShops();
  const { timeZone } = useSession();
  const shop = selected?.id ?? null;
  const [show, setShow] = useState<Show>("needs_work");
  const [page, setPage] = useState(1);
  const [data, setData] = useState<HealthPage | null>(null);
  const [fixes, setFixes] = useState<Fix[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [chosen, setChosen] = useState<number[]>([]);
  const [open, setOpen] = useState<number | null>(null);
  const [approving, setApproving] = useState<string[]>([]);
  const [skipped, setSkipped] = useState<PreviewAnswer["skipped"]>([]);
  const [ask, setAsk] = useState<RewriteAsk | null>(null);
  const [busy, setBusy] = useState(false);
  const [profiles, setProfiles] = useState<{ id: string; name: string }[]>([]);
  const [pickedProfile, setPickedProfile] = useState("");
  const seen = useRef(new Set<string>());
  const latest = useRef(0);

  const load = useCallback(async () => {
    const call = ++latest.current;
    try {
      const [health, done] = await Promise.all([api.listingHealth(shop, page, show), api.listingFixes(shop)]);
      if (call !== latest.current) return;
      setData(health);
      setFixes(done.fixes);
      setError(null);
    } catch (e) {
      if (call === latest.current) setError(e instanceof Error ? e.message : "This page could not be read.");
    }
  }, [shop, page, show]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { setChosen([]); setPage(1); setApproving([]); setPickedProfile(""); seen.current = new Set(); }, [shop]);

  // Attributes are read in the background. A read that cannot finish must not keep this page asking for ever.
  const asks = useRef(0);
  const unread = (data?.listings ?? []).some((l) => !l.attributes_read);
  const working = data?.reading || fixes.some(isWorking) || (unread && asks.current < MAX_ASKS_FOR_ATTRIBUTES);
  useEffect(() => {
    if (!working) return;
    const timer = setInterval(() => { asks.current += 1; void load(); }, 3000);
    return () => clearInterval(timer);
  }, [working, load]);
  useEffect(() => { asks.current = 0; }, [shop, page, show]);

  const waiting = fixes.filter(WAITING);
  const history = fixes.filter((f) => !WAITING(f));
  const cap = data?.max_per_approval ?? 50;
  const undoDays = data?.undo_days ?? 7;
  // A new preview is ticked for approval as it arrives; anything the seller unticked stays unticked.
  useEffect(() => {
    const fresh = waiting.filter((f) => f.state === "proposed" && !seen.current.has(f.id)).map((f) => f.id);
    if (fresh.length) setApproving((was) => [...was, ...fresh].slice(0, cap));
    waiting.forEach((f) => { if (f.state === "proposed") seen.current.add(f.id); });
  }, [waiting, cap]);

  const preview = async (ids: number[], kind: string, accept = false) => {
    if (!data?.connection_id) return;
    setBusy(true);
    setNotice(null);
    try {
      const answer = await api.listingFixPreview(data.connection_id, ids, [kind], accept, pickedProfile || null);
      setSkipped(answer.skipped);
      setAsk(null);
      if (answer.still_reading.length) setNotice(`Reading attributes for ${answer.still_reading.length} of these. Press it again in a moment.`);
      else if (!answer.fixes.length) setNotice("Nothing to change for these.");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "The preview could not be made.");
    } finally {
      setBusy(false);
    }
  };

  const startFix = async (ids: number[], kind: string) => {
    if (!data?.connection_id) return;
    if (kind !== "rewrite") return preview(ids, kind);
    setBusy(true);
    try {
      setAsk({ ids, check: await api.listingFixRewriteCheck(data.connection_id, ids) });
    } catch (e) {
      setError(e instanceof Error ? e.message : "This could not be checked.");
    } finally {
      setBusy(false);
    }
  };

  const act = async (run: () => Promise<{ message: string }>) => {
    setBusy(true);
    try {
      setNotice((await run()).message);
      setApproving([]);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "That did not work.");
    } finally {
      setBusy(false);
    }
  };

  // Two of the seller's profiles fit a listing's category: which one to copy from is theirs to say, never guessed.
  const unclear = (data?.listings ?? []).some((l) => l.profile_unclear);
  useEffect(() => {
    if (!unclear || !shop) return;
    let live = true;
    api.listProfiles(shop).then((list) => { if (live) setProfiles(list.map((p) => ({ id: p.id, name: p.name }))); }).catch(() => undefined);
    return () => { live = false; };
  }, [unclear, shop]);

  const pageIds = (data?.listings ?? []).map((l) => l.listing_id);
  const allOnPage = pageIds.length > 0 && pageIds.every((id) => chosen.includes(id));
  const toggle = (id: number) => setChosen((was) => (was.includes(id) ? was.filter((x) => x !== id) : withinCap([...was, id], cap).ids));

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm text-slate-600">Your active listings against Etsy&apos;s rules and our advice. Nothing changes on Etsy until you approve it.</p>
        {selected && <ShopBadge key="shopbadge-131-10" name={selected.shop_name} />}
        <Help label="About Listing health">
          <span><span><span>{SAID_FIRST}</span> Attributes are read for the listings on screen. Previews last 6 hours; after that, preview again.</span></span>
        </Help>
      </div>

      {error && <p key="p-139-6" role="alert" className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800"><span translate="no">{error}</span></p>}
      {notice && <p key="p-140-6" className="rounded-md border border-slate-200 bg-slate-50 p-3 text-sm text-slate-700"><span translate="no">{notice}</span></p>}

      {!selected && <p key="p-142-6" className="card p-4 text-sm text-slate-700">Connect a shop to see how its listings do.</p>}
      {data?.reading && <p key="p-143-6" className="card p-4 text-sm text-slate-700"><span translate="no"><span><span>Reading </span><Txt>{data.shop_name}</Txt><span>&apos;s listings from Etsy…</span></span></span></p>}

      {data && !data.reading && selected && (
        <>
          <div className="grid gap-3 sm:grid-cols-3">
            <Figure label="Active listings" value={String(data.total)} />
            <Figure label="Checks met, on average" value={data.average === null ? "No listings" : `${data.average}%`} />
            <Figure label="With something to look at" value={String(data.needs_work)} />
          </div>

          {waiting.length > 0 && (
            <section key="section-153-10" className="card space-y-3 p-4" data-testid="health-previews">
              <div>
                <h2 className="font-semibold text-slate-900">Review these changes</h2>
                <p className="text-sm text-slate-600"><span translate="no">{approvalSentence(approving.length, undoDays)}</span></p>
              </div>
              <ul className="space-y-3">
                {waiting.map((fix) => (
                  <li key={fix.id} className="rounded-md border border-slate-200 p-3">
                    <label className="flex items-start gap-2">
                      {fix.state === "proposed" && (
                        <input key="input-163-22" type="checkbox" className="mt-1" checked={approving.includes(fix.id)}
                          onChange={() => setApproving((was) => (was.includes(fix.id) ? was.filter((x) => x !== fix.id) : [...was, fix.id].slice(0, cap)))} />
                      )}
                      <span className="min-w-0 flex-1">
                        <span className="flex flex-wrap items-center gap-2">
                          <a href={fix.url} target="_blank" rel="noreferrer" className="tap break-all text-sm font-medium text-brand-700 underline"><span translate="no"><span>Listing <span>{fix.listing_id}</span></span></span></a>
                          <StatusChip tone={FIX_WORDS[fix.state].tone}>{FIX_WORDS[fix.state].word}</StatusChip>
                        </span>
                        {fix.note && <span key="span-172-24" className="mt-1 block text-sm text-slate-700" translate="no">{fix.note}</span>}
                      </span>
                    </label>
                    {fix.state === "proposed" && <Diff key="diff-175-20" lines={diffOf(fix)} />}
                  </li>
                ))}
              </ul>
              {skipped.length > 0 && (
                <ul key="ul-179-14" className="space-y-1 text-sm text-slate-700" data-testid="health-skipped">
                  {skipped.map((s) => <li key={s.listing_id}><span translate="no"><span><span>{s.title || `Listing ${s.listing_id}`}</span>: <span>{s.reason}</span></span></span></li>)}
                </ul>
              )}
              <div className="flex flex-wrap gap-2">
                <button type="button" className="btn-primary" disabled={busy || approving.length === 0} data-testid="health-approve"
                  onClick={() => void act(() => api.listingFixApprove(approving))}>
                  <span translate="no"><span><span>Approve </span><span>{approving.length}</span><span> change</span><Txt>{approving.length === 1 ? "" : "s"}</Txt></span></span>
                </button>
                <button type="button" className="btn-secondary" disabled={busy}
                  onClick={() => void act(() => api.listingFixDiscard(waiting.filter((f) => f.state !== "writing").map((f) => f.id)))}>
                  Discard
                </button>
              </div>
            </section>
          )}

          {ask && (
            <section key="section-197-10" className="card space-y-2 border-amber-200 bg-amber-50 p-4" data-testid="health-rewrite-rule">
              <h2 className="font-semibold text-slate-900"><span translate="no"><span><span>Rewrite </span><span>{ask.ids.length}</span><span> listing</span><Txt>{ask.ids.length === 1 ? "" : "s"}</Txt><span>?</span></span></span></h2>
              <p className="text-sm text-slate-700"><span translate="no">{ask.check.rule}</span></p>
              <p className="text-sm font-medium text-slate-800"><span translate="no">{rewriteSentence(ask.check)}</span></p>
              <p className="text-sm text-slate-700">You see the new title and tags before anything goes to Etsy.</p>
              <div className="flex flex-wrap gap-2">
                <button type="button" className="btn-secondary" disabled={busy || !ask.check.fits} onClick={() => void preview(ask.ids, "rewrite", true)}>Write the preview</button>
                <button type="button" className="btn-secondary" onClick={() => setAsk(null)}>Not now</button>
              </div>
            </section>
          )}

          <section className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" className={show === "needs_work" ? "btn-secondary font-semibold" : "btn-secondary"} aria-pressed={show === "needs_work"}
                onClick={() => { setShow("needs_work"); setPage(1); }}>Something to look at</button>
              <button type="button" className={show === "all" ? "btn-secondary font-semibold" : "btn-secondary"} aria-pressed={show === "all"}
                onClick={() => { setShow("all"); setPage(1); }}>All active</button>
              {data.listings.length > 0 && (
                <label key="label-216-14" className="tap-row flex items-center gap-2 text-sm text-slate-700">
                  <input type="checkbox" checked={allOnPage} onChange={() => setChosen(allOnPage ? chosen.filter((id) => !pageIds.includes(id)) : withinCap([...chosen, ...pageIds], cap).ids)} />
                  <span>Select this page</span>
                </label>
              )}
            </div>

            {unclear && profiles.length > 0 && (
              <label key="label-238-12" className="card block p-3 text-sm text-slate-700" data-testid="health-pick-profile">
                <span className="block">More than one profile fits some of these. Copy tags and attributes for those from:</span>
                <select className="field mt-1 w-full sm:w-auto" value={pickedProfile} onChange={(e) => setPickedProfile(e.target.value)}>
                  <option value="">Choose a profile</option>
                  {profiles.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              </label>
            )}

            {chosen.length > 0 && (
              <div key="div-224-12" className="card flex flex-wrap items-center gap-2 p-3" data-testid="health-bulk">
                <span className="w-full text-sm font-medium text-slate-800 sm:w-auto"><span translate="no"><span><span>{chosen.length}</span> selected · at most <span>{cap}</span> in one approval</span></span></span>
                {FIX_KINDS.map((f) => (
                  <button key={f.kind} type="button" className="btn-secondary" disabled={busy} title={f.about} onClick={() => void startFix(chosen, f.kind)}>{f.label}</button>
                ))}
              </div>
            )}

            {data.listings.length === 0 && (
              <p key="p-233-12" className="card p-4 text-sm text-slate-700">{show === "needs_work" ? "Nothing to look at: every active listing meets these checks." : "This shop has no active listings."}</p>
            )}

            <ul className="space-y-2">
              {data.listings.map((listing) => (
                <Row key={listing.listing_id} listing={listing} open={open === listing.listing_id} busy={busy} chosen={chosen.includes(listing.listing_id)}
                  onOpen={() => setOpen(open === listing.listing_id ? null : listing.listing_id)} onChoose={() => toggle(listing.listing_id)}
                  onFix={(kind) => void startFix([listing.listing_id], kind)} />
              ))}
            </ul>

            {data.pages > 1 && (
              <div key="div-245-12" className="flex items-center gap-2">
                <button type="button" className="btn-secondary" disabled={data.page <= 1} onClick={() => setPage(data.page - 1)}>Previous</button>
                <span className="text-sm text-slate-700" translate="no"><span>Page <span>{data.page}</span> of <span>{data.pages}</span></span></span>
                <button type="button" className="btn-secondary" disabled={data.page >= data.pages} onClick={() => setPage(data.page + 1)}>Next</button>
              </div>
            )}
          </section>

          {history.length > 0 && (
            <section key="section-254-10" className="card space-y-3 p-4" data-testid="health-history">
              <div>
                <h2 className="font-semibold text-slate-900">Changes made</h2>
                <p className="text-sm text-slate-600"><span translate="no"><span>Undo writes back the previous title, tags and attributes for <span>{undoDays}</span> days. Then the copy is deleted.</span></span></p>
              </div>
              <ul className="space-y-3">
                {history.map((fix) => (
                  <li key={fix.id} className="rounded-md border border-slate-200 p-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <a href={fix.url} target="_blank" rel="noreferrer" className="tap text-sm font-medium text-brand-700 underline"><span translate="no"><span>Listing <span>{fix.listing_id}</span></span></span></a>
                      <StatusChip tone={FIX_WORDS[fix.state].tone}>{FIX_WORDS[fix.state].word}</StatusChip>
                      {fix.applied_at && <span key="span-266-22" className="text-xs text-slate-600" translate="no">{formatWhen(fix.applied_at, timeZone)}</span>}
                    </div>
                    {fix.note && <p key="p-268-20" className="mt-1 text-sm text-slate-700" translate="no">{fix.note}</p>}
                    <Diff lines={diffOf(fix)} />
                    {fix.can_undo && (
                      <div key="div-270-20" className="mt-2 flex flex-wrap items-center gap-2">
                        <button type="button" className="btn-secondary" disabled={busy} onClick={() => void act(() => api.listingFixUndo([fix.id]))}>Undo</button>
                        {fix.undo_until && <span key="span-273-24" className="text-xs text-slate-600" translate="no"><span>until <span>{formatWhen(fix.undo_until, timeZone)}</span></span></span>}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </div>
  );
}

function Figure({ label, value }: { label: string; value: string }) {
  return (
    <div className="card p-3">
      <p className="text-xs font-medium text-slate-600">{label}</p>
      <p className="text-lg font-semibold text-slate-900" translate="no">{value}</p>
    </div>
  );
}

function Row({ listing, open, busy, chosen, onOpen, onChoose, onFix }: {
  listing: HealthListing; open: boolean; busy: boolean; chosen: boolean; onOpen: () => void; onChoose: () => void; onFix: (kind: string) => void;
}) {
  const look = toLookAt(listing);
  return (
    <li className="card p-3" data-testid="health-row">
      <div className="flex items-start gap-3">
        <label className="tap-row flex items-center">
          <input type="checkbox" checked={chosen} aria-label={`Select ${listing.title}`} onChange={onChoose} />
        </label>
        {listing.image && (
          // eslint-disable-next-line @next/next/no-img-element
          <img key="img-306-8" src={listing.image} alt="" loading="lazy" width={56} height={56} className="h-14 w-14 shrink-0 rounded object-cover" />
        )}
        <div className="min-w-0 flex-1">
          <a href={listing.url} target="_blank" rel="noreferrer" className="tap block break-words text-sm font-medium text-brand-700 underline"><span translate="no">{listing.title}</span></a>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-600">
            <StatusChip tone={scoreTone(listing.score)}><span><span><span>{listing.score}</span>% of checks met</span></span></StatusChip>
            {look.length > 0 && <span key="span-314-12" translate="no"><span><span>{look.length}</span> to look at</span></span>}
            {listing.brands.length > 0 && <span key="span-315-12" className="font-medium text-amber-800" translate="no"><span>Brand: <span>{listing.brands.join(", ")}</span></span></span>}
            {listing.profile_name && <span key="span-316-12" translate="no"><span>Profile: <span>{listing.profile_name}</span></span></span>}
            {listing.profile_unclear && <span key="span-317-12" translate="no">More than one profile fits this category</span>}
            {!listing.attributes_read && <span key="span-318-12" translate="no">Reading attributes…</span>}
          </div>
        </div>
        <button type="button" className="tap shrink-0 text-sm font-medium text-brand-700 underline" aria-expanded={open} onClick={onOpen}>{open ? "Close" : "Fix"}</button>
      </div>
      {open && (
        <div key="div-323-6" className="mt-3 space-y-3 border-t border-slate-200 pt-3">
          <div className="flex flex-wrap gap-2">
            {FIX_KINDS.map((f) => (
              <button key={f.kind} type="button" className="btn-secondary" disabled={busy} title={f.about} onClick={() => onFix(f.kind)}>{f.label}</button>
            ))}
          </div>
          <Checks checks={listing.checks} />
        </div>
      )}
    </li>
  );
}

function Checks({ checks }: { checks: HealthCheck[] }) {
  const order = [...checks].sort((a, b) => rank(a) - rank(b));
  return (
    <ul className="space-y-2" data-testid="health-checks">
      {order.map((check) => {
        const rule = RULES[check.id];
        return (
          <li key={check.id} className="text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <StatusChip tone={VERDICT_TONE[check.verdict]}>{VERDICT_WORDS[check.verdict]}</StatusChip>
              <span className="font-medium text-slate-800">{rule?.name ?? check.id}</span>
              <span className="text-xs text-slate-600">{KIND_WORDS[check.kind]}</span>
            </div>
            <p className="mt-0.5 break-words text-slate-700" translate="no">{check.detail}</p>
            {rule?.kind === "etsy" && (
              <p key="p-351-12" className="mt-0.5 text-xs text-slate-600">
                <span><span>&ldquo;<span>{rule.quote}</span>&rdquo; </span></span>
                <a className="tap underline" href={rule.source.url} target="_blank" rel="noreferrer">{rule.source.label}</a>
              </p>
            )}
            {rule?.kind === "advice" && <p key="p-357-12" className="mt-0.5 text-xs text-slate-600">{rule.why}</p>}
          </li>
        );
      })}
    </ul>
  );
}

const rank = (c: HealthCheck) => ({ fail: 0, warn: 1, note: 2, pass: 3 })[c.verdict];

function Diff({ lines }: { lines: DiffLine[] }) {
  if (!lines.length) return null;
  return (
    <dl className="mt-2 space-y-2 text-sm" data-testid="health-diff">
      {lines.map((line) => (
        <div key={line.what}>
          <dt className="text-xs font-medium text-slate-600" translate="no">{line.what}</dt>
          <dd className="mt-0.5 flex flex-wrap gap-1">
            {line.removed.map((text) => <span key={`-${text}`} className="break-words rounded bg-rose-50 px-1.5 py-0.5 text-rose-800 line-through" translate="no">{text}</span>)}
            {line.kept.map((text) => <span key={`=${text}`} className="break-words rounded bg-slate-100 px-1.5 py-0.5 text-slate-700" translate="no">{text}</span>)}
            {line.added.map((text) => <span key={`+${text}`} className="break-words rounded bg-emerald-50 px-1.5 py-0.5 font-medium text-emerald-800" translate="no">{text}</span>)}
          </dd>
        </div>
      ))}
    </dl>
  );
}
