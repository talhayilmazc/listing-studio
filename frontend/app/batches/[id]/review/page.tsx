"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Content } from "@/lib/types";
import { ReviewCard } from "@/components/ReviewCard";

export default function ReviewPage({ params }: { params: { id: string } }) {
  const { id } = params;
  const [items, setItems] = useState<Content[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listContent(id)
      .then(setItems)
      .catch((e) => setError(String(e.message ?? e)));
  }, [id]);

  return (
    <div className="space-y-6">
      <div>
        <Link href={`/batches/${id}`} className="text-sm text-slate-400 hover:text-slate-600">
          ← Batch {id.slice(0, 8)}
        </Link>
        <h1 className="mt-1 text-2xl font-semibold text-slate-900">Review listings</h1>
        <p className="mt-1 text-sm text-slate-500">
          Edit the generated title, 13 tags and description for each design, then approve the ones
          you want turned into drafts.
        </p>
      </div>

      {error && <div className="card p-4 text-sm text-rose-700">{error}</div>}

      {items === null && !error && <p className="text-sm text-slate-400">Loading…</p>}

      {items && items.length === 0 && (
        <div className="card flex flex-col items-center gap-3 p-12 text-center">
          <p className="text-slate-500">No generated content yet for this batch.</p>
          <Link href={`/batches/${id}`} className="btn-primary">
            Generate content
          </Link>
        </div>
      )}

      {items && items.length > 0 && (
        <div className="space-y-5">
          {items.map((c) => (
            <ReviewCard key={c.id} initial={c} />
          ))}
        </div>
      )}
    </div>
  );
}
