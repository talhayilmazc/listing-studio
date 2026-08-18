"use client";

import { useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { Content } from "@/lib/types";
import { TagEditor } from "./TagEditor";

const MAX_TITLE = 140;
const MAX_TAG = 20;
const REQUIRED_TAGS = 13;

/** Mirror of the backend validation, for instant feedback while editing. */
function validate(title: string, tags: string[], description: string): string[] {
  const errors: string[] = [];
  if (!title.trim()) errors.push("Title is empty");
  if (title.length > MAX_TITLE) errors.push(`Title exceeds ${MAX_TITLE} characters`);
  if (tags.length !== REQUIRED_TAGS) errors.push(`Need exactly ${REQUIRED_TAGS} tags`);
  const seen = new Set<string>();
  for (const t of tags) {
    if (!t.trim()) errors.push("A tag is empty");
    if (t.length > MAX_TAG) errors.push(`Tag over ${MAX_TAG} chars: "${t}"`);
    if (t.includes(",")) errors.push(`Tag has a comma: "${t}"`);
    const key = t.trim().toLowerCase();
    if (seen.has(key)) errors.push(`Duplicate tag: "${t}"`);
    seen.add(key);
  }
  if (!description.trim()) errors.push("Description is empty");
  return errors;
}

export function ReviewCard({ initial }: { initial: Content }) {
  const [title, setTitle] = useState(initial.title ?? "");
  const [tags, setTags] = useState<string[]>(initial.tags ?? []);
  const [description, setDescription] = useState(initial.description ?? "");
  const [approved, setApproved] = useState(initial.approved);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const errors = useMemo(() => validate(title, tags, description), [title, tags, description]);
  const valid = errors.length === 0;

  async function save() {
    setSaving(true);
    setMessage(null);
    try {
      await api.updateContent(initial.id, { title, tags, description });
      setDirty(false);
      setMessage("Saved");
    } catch (e: any) {
      setMessage(e.message ?? String(e));
    } finally {
      setSaving(false);
    }
  }

  async function toggleApprove() {
    if (dirty) await save();
    try {
      const res = await api.approve(initial.id, !approved);
      setApproved(res.content.approved);
      setMessage(res.content.approved ? "Approved" : "Approval cleared");
    } catch (e: any) {
      setMessage(e.message ?? String(e));
    }
  }

  const change =
    <T,>(setter: (v: T) => void) =>
    (v: T) => {
      setter(v);
      setDirty(true);
    };

  return (
    <div
      className={`card overflow-hidden ${approved ? "ring-2 ring-emerald-300" : ""}`}
      id={initial.id}
    >
      <div className="grid gap-0 md:grid-cols-[220px_1fr]">
        {/* Image + meta */}
        <div className="border-b border-slate-100 bg-slate-50 md:border-b-0 md:border-r">
          <div className="aspect-square">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={api.assetImage(initial.asset_id)}
              alt={initial.original_filename}
              className="h-full w-full object-cover"
            />
          </div>
          <div className="space-y-1 p-3 text-xs text-slate-500">
            <div className="truncate font-medium text-slate-700" title={initial.original_filename}>
              {initial.original_filename}
            </div>
            <div>SKU: {initial.parsed_sku ?? "—"}</div>
            <div>Rank: {initial.rank ?? "—"}</div>
            {initial.model_used && <div className="truncate">Model: {initial.model_used}</div>}
          </div>
        </div>

        {/* Editable fields */}
        <div className="space-y-4 p-5">
          <div>
            <div className="mb-1 flex items-center justify-between">
              <span className="label mb-0">Title</span>
              <span
                className={`text-xs font-medium ${
                  title.length > MAX_TITLE ? "text-rose-600" : "text-slate-400"
                }`}
              >
                {title.length} / {MAX_TITLE}
              </span>
            </div>
            <input
              className="field"
              value={title}
              onChange={(e) => change(setTitle)(e.target.value)}
              placeholder="Listing title"
            />
          </div>

          <TagEditor tags={tags} onChange={change(setTags)} />

          <div>
            <span className="label">Description</span>
            <textarea
              className="field min-h-[120px] resize-y"
              value={description}
              onChange={(e) => change(setDescription)(e.target.value)}
              placeholder="Listing description"
            />
          </div>

          {!valid && (
            <ul className="space-y-0.5 rounded-lg bg-rose-50 p-3 text-xs text-rose-700">
              {errors.map((e, i) => (
                <li key={i}>• {e}</li>
              ))}
            </ul>
          )}

          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-4">
            <div className="flex items-center gap-3">
              <button className="btn-secondary" onClick={save} disabled={saving || !dirty}>
                {saving ? "Saving…" : dirty ? "Save changes" : "Saved"}
              </button>
              <label className="flex cursor-pointer items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={approved}
                  disabled={!valid}
                  onChange={toggleApprove}
                  className="h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
                />
                <span className={approved ? "font-medium text-emerald-700" : "text-slate-600"}>
                  {approved ? "Approved" : "Approve for draft"}
                </span>
              </label>
              {message && <span className="text-xs text-slate-400">{message}</span>}
            </div>

            <div className="flex items-center gap-2">
              <button
                type="button"
                disabled
                title="Publishing arrives with the Etsy client step."
                className="btn-secondary"
              >
                Publish to Etsy
              </button>
            </div>
          </div>
          <p className="text-right text-xs text-slate-400">
            Publishing to Etsy arrives with the Etsy client step; drafts are created there.
          </p>
        </div>
      </div>
    </div>
  );
}
