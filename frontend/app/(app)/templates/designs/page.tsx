"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { MockupUpload } from "@/components/MockupUpload";
import { useShops } from "@/components/ShopProvider";

/**
 * "Add designs": the step after the template's photos are confirmed (backend
 * api/mockups.py). Many designs at once (files, a folder or a ZIP), the templates chosen
 * (those of the link start ticked), "Create mockups": every design lands in the confirmed
 * print areas of every photo, grouped per design, then "Use for listings" or "Download ZIP".
 * Nothing here calls Etsy or an AI model.
 */
export default function AddDesignsPage() {
  return (
    <Suspense fallback={<section className="card h-[11rem] animate-pulse" aria-hidden />}>
      <AddDesigns />
    </Suspense>
  );
}

function AddDesigns() {
  const params = useSearchParams();
  const ids = (params.get("templates") ?? "").split(",").map((s) => s.trim()).filter(Boolean);
  const { selected } = useShops();
  return (
    <div className="space-y-6">
      <Link href="/templates" className="tap inline-block text-sm text-slate-500 hover:text-slate-700">
        ← Mockup templates
      </Link>
      {/* From a "Mockups ready" notification: that run's results, scrolled to the design. */}
      <MockupUpload key={params.get("run") ?? "new"} shop={selected?.id ?? null} initialTemplates={ids}
        initialRun={params.get("run")} initialDesign={params.get("design")} />
    </div>
  );
}
