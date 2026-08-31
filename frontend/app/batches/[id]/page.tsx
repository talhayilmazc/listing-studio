"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { Asset, BatchDetail, Profile } from "@/lib/types";
import { StatusPill } from "@/components/StatusPill";
import { CostPanel } from "@/components/CostPanel";

interface Group {
  key: string;
  label: string;
  sku: string | null;
  assets: Asset[];
  done: boolean;
}

export default function BatchPage({ params }: { params: { id: string } }) {
  const { id } = params;
  const [batch, setBatch] = useState<BatchDetail | null>(null);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [profileId, setProfileId] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null); // group key, or "__all__"
  const [notice, setNotice] = useState<string | null>(null);
  const [failures, setFailures] = useState<{ original_filename: string; error: string }[]>([]);
  const [costKey, setCostKey] = useState(0);

  async function load() {
    try {
      setBatch(await api.getBatch(id));
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }

  useEffect(() => {
    load();
    api
      .listProfiles()
      .then((all) => {
        const confirmed = all.filter((p) => p.confirmed);
        setProfiles(confirmed);
        if (confirmed[0]) setProfileId((cur) => cur || confirmed[0].id);
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  // One folder = one listing group (D1).
  const groups: Group[] = useMemo(() => {
    if (!batch) return [];
    const by = new Map<string, Asset[]>();
    for (const a of batch.assets) {
      const key = a.group_key ?? "";
      (by.get(key) ?? by.set(key, []).get(key)!).push(a);
    }
    return [...by.entries()]
      .map(([key, assets]) => ({
        key,
        label: key === "" ? "(root)" : key,
        sku: assets.find((a) => a.parsed_sku)?.parsed_sku ?? null,
        assets: assets.sort((a, b) => (a.rank ?? 0) - (b.rank ?? 0)),
        done: assets.some((a) => a.has_content),
      }))
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [batch]);

  const selected = profiles.find((p) => p.id === profileId) ?? null;

  async function generate(groupKey?: string) {
    if (!profileId) {
      setNotice("Pick a profile first.");
      return;
    }
    setBusy(groupKey ?? "__all__");
    setNotice(null);
    setFailures([]);
    try {
      const res = await api.generate(id, profileId, groupKey);
      setNotice(`Generated ${res.generated}, failed ${res.failed}, skipped ${res.skipped}.`);
      setFailures(res.failures);
      await load();
      setCostKey((k) => k + 1);
    } catch (e: any) {
      setNotice(e.message ?? String(e));
    } finally {
      setBusy(null);
    }
  }

  if (error) return <div className="card p-4 text-sm text-rose-700">{error}</div>;
  if (!batch) return <p className="text-sm text-slate-400">Loading…</p>;

  const withContent = batch.assets.filter((a) => a.has_content).length;
  const anyBusy = busy !== null;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link href="/" className="text-sm text-slate-400 hover:text-slate-600">
            ← Batches
          </Link>
          <div className="mt-1 flex items-center gap-2">
            <h1 className="text-2xl font-semibold text-slate-900">Batch {id.slice(0, 8)}</h1>
            <StatusPill status={batch.status} />
          </div>
          <p className="mt-1 text-sm text-slate-500">
            {groups.length} listing group{groups.length === 1 ? "" : "s"} · {batch.processed_count}{" "}
            processed · {withContent} with content · {batch.approved_count} approved
          </p>
        </div>
        <Link href={`/batches/${id}/review`} className="btn-primary">
          Review listings
        </Link>
      </div>

      {/* Profile picker + Generate all */}
      <div className="card flex flex-wrap items-center justify-between gap-3 p-4">
        <div className="flex items-center gap-2">
          <label className="text-sm text-slate-600">Profile</label>
          {profiles.length === 0 ? (
            <Link href="/profiles" className="text-sm text-brand-600 underline">
              Create a profile first →
            </Link>
          ) : (
            <select
              className="field w-auto py-1 text-sm"
              value={profileId}
              onChange={(e) => setProfileId(e.target.value)}
            >
              {profiles.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} ({p.content_template})
                </option>
              ))}
            </select>
          )}
          {selected && !selected.is_fresh && (
            <span className="text-xs text-amber-600">reference not fetched — refresh it first</span>
          )}
          {profiles.length > 0 && (
            <>
              <label className="ml-3 text-sm text-slate-600">Size charts</label>
              <select
                className="field w-auto py-1 text-sm"
                value={batch.size_chart_profile_id ?? ""}
                onChange={async (e) => {
                  const val = e.target.value || null;
                  try {
                    const updated = await api.setSizeChartProfile(id, val);
                    setBatch((b) => (b ? { ...b, size_chart_profile_id: updated.size_chart_profile_id } : b));
                  } catch (err: any) {
                    setNotice(err.message ?? String(err));
                  }
                }}
              >
                <option value="">Each listing’s own profile</option>
                {profiles.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </>
          )}
        </div>
        <button
          className="btn-secondary"
          onClick={() => generate()}
          disabled={anyBusy || !profileId}
        >
          {busy === "__all__" ? "Generating all…" : "Generate content for all"}
        </button>
      </div>

      {notice && (
        <div className="card border-brand-100 bg-brand-50 p-3 text-sm text-brand-800">{notice}</div>
      )}

      {failures.length > 0 && (
        <div className="card border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
          <p className="font-medium">Generation failures</p>
          <ul className="mt-1 space-y-1">
            {failures.map((f, i) => (
              <li key={i} className="break-words">
                <span className="font-mono text-xs">{f.original_filename}</span>: {f.error}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Detected groups */}
      <div className="space-y-3">
        {groups.map((g) => (
          <div key={g.key} className="card p-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <span className="rounded bg-slate-100 px-2 py-0.5 text-sm text-slate-600">
                  {g.label}
                </span>
                {g.sku && (
                  <span className="font-mono text-xs text-slate-500">SKU {g.sku}</span>
                )}
                <span className="text-xs text-slate-400">
                  {g.assets.length} image{g.assets.length === 1 ? "" : "s"}
                </span>
                {g.done && <span className="text-xs text-emerald-600">✓ content ready</span>}
              </div>
              <button
                className="btn-secondary py-1 text-xs"
                onClick={() => generate(g.key)}
                disabled={anyBusy || !profileId}
              >
                {busy === g.key ? "Generating…" : g.done ? "Regenerate" : "Generate"}
              </button>
            </div>
            <div className="mt-3 flex gap-2 overflow-x-auto">
              {g.assets.map((a) => (
                <div key={a.id} className="shrink-0">
                  <div className="h-16 w-16 overflow-hidden rounded bg-slate-100">
                    {a.status === "processed" ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        src={api.assetImage(a.id)}
                        alt={a.original_filename}
                        className="h-full w-full object-cover"
                      />
                    ) : null}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      <CostPanel batchId={id} refreshKey={costKey} />
    </div>
  );
}
