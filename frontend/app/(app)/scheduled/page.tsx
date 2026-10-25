"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "@/lib/api";
import {
  BULK_LABELS,
  NO_FILTERS,
  STATUS_LABELS,
  allIn,
  byDay,
  dayLabel,
  dragged,
  filterItems,
  groupLine,
  movableKeys,
  settingsLine,
  toggleMany,
  wallValue,
  type BulkOp,
  type Filters,
  type GroupSettings,
  type ScheduledData,
  type ScheduledGroup,
  type ScheduledItem,
} from "@/lib/scheduled";
import type { BatchSummary, ScheduledApplied, ScheduledPreview } from "@/lib/types";
import { ZoneNote } from "@/components/ZoneNote";
import { WhereAndWhen } from "@/components/WhereAndWhen";
import { useSession } from "@/components/SessionProvider";
import { Txt } from "@/components/Txt";
import { Help } from "@/components/Help";
import { StatusChip, type Tone } from "@/components/StatusChip";

// Each status in the one status chip every page uses (B9).
const TONE: Record<string, Tone> = {
  planned: "neutral",
  drafting: "working",
  held: "attention",
  scheduled: "working",
  missed: "attention",
  publishing: "working",
  waiting: "attention",
  published: "done",
  failed: "failed",
  not_published: "failed",
};

const DRAG = "application/x-listyro-scheduled";

/**
 * Every scheduled go-live (B8): per-group summaries with pause and settings, filters, a
 * timeline by day, and editing one by one (each row's own date and time, or a drag to another
 * day) or in bulk (Set time, Shift by, Spread between, Move to day, Publish now, Remove), with a
 * preview of old -> new before saving, then one Save and one Undo. The server keeps the rules
 * (minutes between listings in a shop, a shop's daily limit across groups, one design never in
 * two shops at the same minute) and says in one line what it moved. Times are the seller's.
 */
export default function ScheduledPage() {
  const [data, setData] = useState<ScheduledData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [selected, setSelected] = useState<string[]>([]);
  const [result, setResult] = useState<{ text: string; undo: ScheduledApplied["undo"] } | null>(null);
  const [busy, setBusy] = useState(false);
  const [scheduling, setScheduling] = useState(false);

  const load = useCallback(() => {
    api.scheduled().then((d) => {
      setData(d);
      setSelected((cur) => cur.filter((k) => d.items.some((i) => i.key === k && i.movable)));
    }).catch((e) => setError(String(e.message ?? e)));
  }, []);

  useEffect(() => {
    load();
    // Due go-lives turn into "Publishing" and then "Published" by themselves.
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, [load]);

  const shown = useMemo(() => (data ? filterItems(data.items, filters, data.groups) : []), [data, filters]);
  const days = useMemo(() => byDay(shown), [shown]);
  const shownKeys = movableKeys(shown);

  // Saved at once (a row's own time, a drag): the result line says what moved, with Undo.
  async function applyNow(op: string, keys: string[], params: Record<string, unknown> = {}) {
    setBusy(true);
    setError(null);
    try {
      const res = await api.scheduledApply(op, keys, params);
      setResult({ text: [res.sentence, res.moved_line].filter(Boolean).join(" "), undo: res.undo });
      load();
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  }

  async function undo() {
    if (!result) return;
    setBusy(true);
    try {
      const res = await api.scheduledRestore(result.undo);
      setResult({ text: res.sentence, undo: [] });
      load();
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  }

  async function pauseAll(paused: boolean) {
    setBusy(true);
    try {
      setData(await api.pauseAllSchedules(paused));
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  }

  const upcoming = data?.items.filter((i) => i.movable).length ?? 0;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <p className="max-w-2xl text-sm text-slate-500">
          <span>Every listing waiting to go live, in your time zone.</span>
          <Help label="scheduling">
            Change one time or many; each goes live only at its time, and approval and Etsy&apos;s rules are checked again
            then. After every change the times keep the minutes between listings in a shop, each shop&apos;s listings a
            day across all your groups, and never the same design in two shops in the same minute; anything moved further
            is said in one line. Pause holds listings without cancelling them.
          </Help>
        </p>
        <div className="flex flex-wrap items-center gap-2">
          {data && (
            <button key="pause-all" type="button" className="btn-secondary" disabled={busy} onClick={() => pauseAll(!data.paused_all)}
              data-testid="pause-all">
              {data.paused_all ? "Resume all" : "Pause all"}
            </button>
          )}
          <button type="button" className="btn-primary" onClick={() => setScheduling(true)} data-testid="schedule-listings">
            Schedule listings
          </button>
        </div>
      </div>
      <ZoneNote />
      {data?.paused_all && (
        <p key="paused-all" role="status" className="card border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
          Everything is paused: nothing goes live until you resume. A time that passes meanwhile goes at once when you resume.
        </p>
      )}

      {data && data.groups.length > 0 && (
        <section key="groups" className="grid gap-3 md:grid-cols-2 2xl:grid-cols-3" data-testid="group-summaries">
          {data.groups.map((g) => (
            <GroupCard key={g.id} group={g} busy={busy} onChanged={setData} onError={setError} reload={load}
              onSelect={() => setSelected((cur) => toggleMany(cur, movableKeys(data.items.filter((i) => g.shop_ids.includes(i.shop_id))), true))} />
          ))}
        </section>
      )}

      {data && (
        <div key="filters" className="flex flex-wrap items-end gap-2 text-sm" data-testid="scheduled-filters">
          <FilterSelect label="Group" value={filters.group} onChange={(v) => setFilters({ ...filters, group: v })}
            options={data.groups.map((g) => [g.id, g.name])} />
          <FilterSelect label="Shop" value={filters.shop} onChange={(v) => setFilters({ ...filters, shop: v })}
            options={data.shops.map((s) => [s.id, s.name])} />
          <FilterSelect label="Profile" value={filters.profile} onChange={(v) => setFilters({ ...filters, profile: v })}
            options={data.profiles.map((p) => [p.id, p.name])} />
          <FilterSelect label="Status" value={filters.status} onChange={(v) => setFilters({ ...filters, status: v })}
            options={Object.entries(STATUS_LABELS)} />
          <label className="flex min-h-[44px] items-center gap-2 text-slate-700 sm:min-h-0">
            <input type="checkbox" className="h-4 w-4" checked={allIn(selected, shownKeys)} disabled={!shownKeys.length}
              onChange={(e) => setSelected((cur) => toggleMany(cur, shownKeys, e.target.checked))} data-testid="select-all-shown" />
            <span>Select all shown</span>
          </label>
        </div>
      )}

      {selected.length > 0 && data && (
        <BulkBar key="bulk" selected={selected} items={data.items} onClear={() => setSelected([])}
          onSaved={(r) => {
            setResult(r);
            load();
          }} />
      )}

      {result && (
        <div key="result" role="status" translate="no" className="card flex flex-wrap items-center gap-3 p-3 text-sm text-slate-800" data-testid="scheduled-result">
          <span>{result.text}</span>
          {result.undo.length > 0 && (
            <button key="undo" type="button" className="btn-secondary px-3 py-1.5 text-xs" disabled={busy} onClick={undo} data-testid="undo">
              Undo
            </button>
          )}
          <button type="button" className="tap text-xs text-slate-500 underline" onClick={() => setResult(null)}>Dismiss</button>
        </div>
      )}

      {error && <div key="error" className="card p-3 text-sm text-rose-700"><Txt>{error}</Txt></div>}
      {data === null && !error && <p key="loading" className="text-sm text-slate-400">Loading…</p>}

      {data && data.items.length === 0 && (
        <div key="empty" className="card p-6 text-sm text-slate-600">
          <span>Nothing is scheduled. Use </span><b>Schedule listings</b><span> to spread a batch&apos;s approved listings out.</span>
        </div>
      )}
      {data && data.items.length > 0 && (
        <p key="count" className="text-xs text-slate-500">
          <span translate="no" className="font-medium tabular-nums text-slate-700">{upcoming}</span>
          <span> still to go live · finished ones stay here for a week · drag a row onto another day to move it</span>
        </p>
      )}

      {days.map(([day, list]) => {
        const keys = movableKeys(list);
        return (
          <section key={day} className="space-y-2" data-testid="scheduled-day"
            onDragOver={(e) => e.dataTransfer.types.includes(DRAG) && e.preventDefault()}
            onDrop={(e) => {
              const key = e.dataTransfer.getData(DRAG);
              if (key) applyNow("move_day", dragged(selected, key), { date: day });
            }}>
            <h2 className="flex items-center gap-2 border-b border-slate-200 pb-1 font-display text-lg text-slate-900">
              {keys.length > 0 && (
                <input key="day-box" type="checkbox" className="h-4 w-4" checked={allIn(selected, keys)}
                  onChange={(e) => setSelected((cur) => toggleMany(cur, keys, e.target.checked))} aria-label={`Select ${dayLabel(day)}`}
                  data-testid="select-day" />
              )}
              <span>{dayLabel(day)}</span>
              <span translate="no" className="text-xs font-normal text-slate-500">{list.length}</span>
            </h2>
            <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white">
              {list.map((item) => (
                <Row key={item.key} item={item} selected={selected.includes(item.key)} busy={busy}
                  multiShop={(data?.shops.length ?? 0) > 1}
                  onSelect={(on) => setSelected((cur) => toggleMany(cur, [item.key], on))}
                  onTime={(value) => applyNow("times", [item.key], { times: { [item.key]: value } })} />
              ))}
            </ul>
          </section>
        );
      })}

      {scheduling && <ScheduleListings key="schedule" onClose={() => {
        setScheduling(false);
        load();
      }} />}
    </div>
  );
}

function FilterSelect({ label, value, onChange, options }: { label: string; value: string; onChange: (v: string) => void; options: [string, string][] }) {
  return (
    <label className="space-y-0.5">
      <span className="label block">{label}</span>
      <select className="field w-auto py-1.5 text-sm" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">All</option>
        {options.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
      </select>
    </label>
  );
}

function Row({ item, selected, busy, multiShop, onSelect, onTime }: {
  item: ScheduledItem;
  selected: boolean;
  busy: boolean;
  multiShop: boolean;
  onSelect: (on: boolean) => void;
  onTime: (value: string) => void;
}) {
  const [value, setValue] = useState(wallValue(item));
  useEffect(() => setValue(wallValue(item)), [item]);
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-sm" data-testid="scheduled-row"
      draggable={item.movable} onDragStart={(e) => {
        e.dataTransfer.setData(DRAG, item.key);
        e.dataTransfer.effectAllowed = "move";
      }}>
      <input type="checkbox" className="h-4 w-4" checked={selected} disabled={!item.movable} onChange={(e) => onSelect(e.target.checked)}
        aria-label={`Select ${item.title}`} data-testid="select-row" />
      {item.movable ? (
        <input key="when" type="datetime-local" className="field w-auto py-1 text-sm" value={value} disabled={busy}
          onChange={(e) => setValue(e.target.value)}
          onBlur={() => value && value !== wallValue(item) && onTime(value)}
          onKeyDown={(e) => e.key === "Enter" && value && value !== wallValue(item) && onTime(value)}
          aria-label={`When ${item.title} goes live`} data-testid="row-time" />
      ) : (
        <span key="when" translate="no" className="tabular-nums text-slate-700">{item.local}</span>
      )}
      <span className="min-w-0 flex-1 truncate text-slate-900" title={item.title}>{item.title}</span>
      {multiShop && <span key="shop" translate="no" className="max-w-[10rem] truncate text-xs text-slate-600" title={item.shop_name}>{item.shop_name}</span>}
      {item.profile_name && <span key="profile" translate="no" className="hidden max-w-[10rem] truncate text-xs text-slate-500 sm:inline" title={item.profile_name}>{item.profile_name}</span>}
      <StatusChip tone={TONE[item.status] ?? "neutral"}>{STATUS_LABELS[item.status] ?? item.status}</StatusChip>
      {item.paused && item.movable && <span key="paused" className="text-[11px] text-amber-800">paused</span>}
      {item.listing_link && (
        <a key="link" href={item.listing_link} target="_blank" rel="noreferrer" className="tap text-xs text-brand-700 underline">Etsy ↗</a>
      )}
      {item.note && <span key="note" className="basis-full text-xs text-slate-600"><Txt>{item.note}</Txt></span>}
    </li>
  );
}

/** The bulk actions on the selection: a small form, the preview (old -> new), one Save. */
function BulkBar({ selected, items, onClear, onSaved }: {
  selected: string[];
  items: ScheduledItem[];
  onClear: () => void;
  onSaved: (r: { text: string; undo: ScheduledApplied["undo"] }) => void;
}) {
  const [op, setOp] = useState<BulkOp | null>(null);
  const first = items.filter((i) => selected.includes(i.key)).sort((a, b) => a.at.localeCompare(b.at))[0];
  const [params, setParams] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<ScheduledPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function start(next: BulkOp) {
    setOp(next);
    setPreview(null);
    setError(null);
    const at = first ? wallValue(first) : "";
    setParams({ local_time: at, minutes_between: "30", minutes: "60", from: at, to: at, date: first?.local_day ?? "" });
    if (next === "publish_now" || next === "remove") ask(next, {});
  }

  function body(which: BulkOp, p: Record<string, string>): Record<string, unknown> {
    if (which === "set_time") return { local_time: p.local_time, minutes_between: Number(p.minutes_between || 0) };
    if (which === "shift") return { minutes: Number(p.minutes || 0) };
    if (which === "spread") return { from: p.from, to: p.to };
    if (which === "move_day") return { date: p.date };
    return {};
  }

  async function ask(which: BulkOp, p: Record<string, string>) {
    setBusy(true);
    setError(null);
    try {
      setPreview(await api.scheduledPreview(which, selected, body(which, p)));
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (!op) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.scheduledApply(op, selected, body(op, params));
      onSaved({ text: [res.sentence, res.moved_line].filter(Boolean).join(" "), undo: res.undo });
      setOp(null);
      setPreview(null);
      onClear();
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  }

  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) => setParams((cur) => ({ ...cur, [k]: e.target.value }));
  return (
    <div className="card sticky top-2 z-10 space-y-3 p-3 text-sm shadow-sm" data-testid="bulk-bar">
      <div className="flex flex-wrap items-center gap-2">
        <span translate="no" className="font-medium text-slate-800">{`${selected.length} selected`}</span>
        {(Object.keys(BULK_LABELS) as BulkOp[]).map((b) => (
          <button key={b} type="button" disabled={busy} onClick={() => start(b)} data-testid={`bulk-${b}`}
            className={(op === b ? "btn-primary" : b === "remove" ? "btn-danger" : "btn-secondary") + " px-3 py-1.5 text-xs"}>
            {BULK_LABELS[b]}
          </button>
        ))}
        <button type="button" className="tap text-xs text-slate-500 underline" onClick={onClear}>Clear</button>
      </div>
      {op && !["publish_now", "remove"].includes(op) && (
        <div key="form" className="flex flex-wrap items-end gap-2">
          {op === "set_time" && (
            <>
              <label key="t" className="space-y-0.5"><span className="label block">First at</span>
                <input type="datetime-local" className="field w-auto py-1" value={params.local_time} onChange={set("local_time")} /></label>
              <label key="m" className="space-y-0.5"><span className="label block">Then minutes between</span>
                <input type="number" min={0} className="field w-24 py-1" value={params.minutes_between} onChange={set("minutes_between")} /></label>
            </>
          )}
          {op === "shift" && (
            <label key="s" className="space-y-0.5"><span className="label block">Minutes (negative: earlier)</span>
              <input type="number" className="field w-28 py-1" value={params.minutes} onChange={set("minutes")} /></label>
          )}
          {op === "spread" && (
            <>
              <label key="f" className="space-y-0.5"><span className="label block">From</span>
                <input type="datetime-local" className="field w-auto py-1" value={params.from} onChange={set("from")} /></label>
              <label key="u" className="space-y-0.5"><span className="label block">Until</span>
                <input type="datetime-local" className="field w-auto py-1" value={params.to} onChange={set("to")} /></label>
            </>
          )}
          {op === "move_day" && (
            <label key="d" className="space-y-0.5"><span className="label block">Day (each keeps its time)</span>
              <input type="date" className="field w-auto py-1" value={params.date} onChange={set("date")} /></label>
          )}
          <button type="button" className="btn-secondary px-3 py-1.5 text-xs" disabled={busy} onClick={() => ask(op, params)} data-testid="bulk-preview">
            Preview
          </button>
        </div>
      )}
      {preview && (
        <div key="preview" className="space-y-2" data-testid="bulk-preview-list">
          <p translate="no" className="font-medium text-slate-900"><span>{preview.sentence}</span></p>
          {preview.moved_line && <p key="moved" className="text-xs text-amber-900">{preview.moved_line}</p>}
          <ul className="max-h-64 space-y-0.5 overflow-y-auto text-xs text-slate-700">
            {preview.rows.map((r) => (
              <li key={r.key} translate="no" className="flex flex-wrap gap-x-2">
                <span className="min-w-0 max-w-[16rem] truncate font-medium" title={r.title}>{r.title}</span>
                <span className="text-slate-500">{r.shop_name}</span>
                <span>{r.removed ? `${r.old_local} → removed` : `${r.old_local} → ${r.new_local}`}</span>
                {r.moved && <span key="m" className="text-amber-800">(moved by the rules)</span>}
              </li>
            ))}
          </ul>
          <button type="button" className="btn-primary px-3 py-1.5 text-xs" disabled={busy || preview.rows.length === 0} onClick={save}
            data-testid="bulk-save">
            Save
          </button>
        </div>
      )}
      {error && <p key="error" className="text-xs text-rose-700"><Txt>{error}</Txt></p>}
    </div>
  );
}

/** A group's summary, pause, selection and settings (with a reflow preview). */
function GroupCard({ group, busy, onChanged, onError, reload, onSelect }: {
  group: ScheduledGroup;
  busy: boolean;
  onChanged: (d: ScheduledData) => void;
  onError: (e: string) => void;
  reload: () => void;
  onSelect: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [settings, setSettings] = useState<GroupSettings>(group.settings ?? {
    per_shop_per_day: 5, window_start: "09:00", window_end: "17:00", spacing_minutes: 30, stagger_minutes: 5,
  });
  const [preview, setPreview] = useState<ScheduledPreview | null>(null);
  const [working, setWorking] = useState(false);

  async function pause() {
    try {
      onChanged(await api.setGroupSchedule(group.id, { paused: !group.paused }));
    } catch (e: any) {
      onError(e.message ?? String(e));
    }
  }

  async function reflow(apply: boolean) {
    setWorking(true);
    try {
      const res = await api.reflowGroup(group.id, settings, apply);
      if (apply) {
        setEditing(false);
        setPreview(null);
        reload();
      } else setPreview(res);
    } catch (e: any) {
      onError(e.message ?? String(e));
    } finally {
      setWorking(false);
    }
  }

  const num = (k: keyof GroupSettings) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setSettings((s) => ({ ...s, [k]: k === "window_start" || k === "window_end" ? e.target.value : Math.max(k === "stagger_minutes" ? 0 : 1, Number(e.target.value) || 0) }));
  return (
    <div className="card space-y-2 p-3 text-sm" data-testid="group-summary">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate font-medium text-slate-900" translate="no" title={group.name}>{group.name}</p>
          <p translate="no" className="text-xs text-slate-600"><span>{groupLine(group)}</span></p>
          <p className="text-xs text-slate-500"><span translate="no">{settingsLine(group.settings)}</span></p>
        </div>
        {group.paused && <span key="paused" className="shrink-0 rounded border border-amber-200 bg-amber-50 px-1.5 text-[11px] text-amber-800">Paused</span>}
      </div>
      <div className="flex flex-wrap gap-2">
        <button type="button" className="btn-secondary px-3 py-1.5 text-xs" disabled={busy} onClick={pause} data-testid="group-pause">
          {group.paused ? "Resume" : "Pause"}
        </button>
        <button type="button" className="btn-secondary px-3 py-1.5 text-xs" disabled={!group.upcoming} onClick={onSelect}>Select</button>
        <button type="button" className="btn-secondary px-3 py-1.5 text-xs" onClick={() => setEditing((v) => !v)} aria-expanded={editing}
          data-testid="group-settings">
          {editing ? "Close settings" : "Settings"}
        </button>
      </div>
      {editing && (
        <div key="settings" className="space-y-2 border-t border-slate-100 pt-2">
          <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-3">
            <label className="space-y-0.5"><span className="label block">A day per shop</span>
              <input type="number" min={1} max={50} className="field py-1" value={settings.per_shop_per_day} onChange={num("per_shop_per_day")} /></label>
            <label className="space-y-0.5"><span className="label block">Minutes apart</span>
              <input type="number" min={1} className="field py-1" value={settings.spacing_minutes} onChange={num("spacing_minutes")} /></label>
            <label className="space-y-0.5"><span className="label block">Shops apart (min)</span>
              <input type="number" min={0} className="field py-1" value={settings.stagger_minutes} onChange={num("stagger_minutes")} /></label>
            <label className="space-y-0.5"><span className="label block">From</span>
              <input type="time" className="field py-1" value={settings.window_start} onChange={num("window_start")} /></label>
            <label className="space-y-0.5"><span className="label block">Until</span>
              <input type="time" className="field py-1" value={settings.window_end} onChange={num("window_end")} /></label>
          </div>
          <button type="button" className="btn-secondary px-3 py-1.5 text-xs" disabled={working} onClick={() => reflow(false)} data-testid="reflow-preview">
            Preview the new times
          </button>
          {preview && (
            <div key="preview" className="space-y-1 text-xs">
              <p translate="no" className="font-medium"><span>{preview.sentence}</span></p>
              {preview.moved_line && <p key="moved" className="text-amber-900">{preview.moved_line}</p>}
              <ul className="max-h-48 space-y-0.5 overflow-y-auto text-slate-700">
                {preview.rows.map((r) => (
                  <li key={r.key} translate="no"><span>{`${r.title} · ${r.shop_name}: ${r.old_local} → ${r.new_local}`}</span></li>
                ))}
              </ul>
              <button type="button" className="btn-primary px-3 py-1.5 text-xs" disabled={working} onClick={() => reflow(true)} data-testid="reflow-save">
                Save settings and times
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** "Schedule listings": choose a batch with approved listings, then its "Where and when". */
function ScheduleListings({ onClose }: { onClose: () => void }) {
  const { timeZone } = useSession();
  const [batches, setBatches] = useState<BatchSummary[] | null>(null);
  const [batch, setBatch] = useState<string>("");
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => {
    api.listBatches().then((b) => setBatches(b.filter((x) => x.approved_count > 0))).catch(() => setBatches([]));
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 sm:p-6" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label="Schedule listings" onClick={(e) => e.stopPropagation()}
        className="flex h-full w-full max-w-3xl flex-col gap-3 overflow-y-auto bg-white p-4 shadow-xl sm:h-auto sm:max-h-[90vh] sm:rounded-lg">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-sm font-semibold text-slate-900">Schedule listings</h2>
          <button type="button" className="btn-secondary px-3 py-1.5 text-xs" onClick={onClose}>Close</button>
        </div>
        <label className="space-y-0.5 text-sm">
          <span className="label block">Batch</span>
          <select className="field" value={batch} onChange={(e) => setBatch(e.target.value)} data-testid="schedule-batch">
            <option value="">{batches === null ? "Loading…" : batches.length ? "Choose a batch with approved listings…" : "No batch has approved listings yet"}</option>
            {(batches ?? []).map((b) => <option key={b.id} value={b.id}>{`${b.name} (${b.approved_count} approved)`}</option>)}
          </select>
        </label>
        {batch && (
          <WhereAndWhen key={batch} batchId={batch} timeZone={timeZone} refreshKey="" disabled={false}
            onState={() => undefined} onPreview={() => undefined}
            onDrafts={async (call) => {
              const res = await call();
              setNote(`Creating ${res.jobs.length} draft${res.jobs.length === 1 ? "" : "s"}.`);
            }}
            onDone={() => setNote("Scheduled. The listings are on this page now.")} />
        )}
        {note && <p key="note" role="status" className="text-sm text-emerald-800">{note}</p>}
      </div>
    </div>,
    document.body,
  );
}
