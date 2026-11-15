"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { homeSalesCard, type HealthSales } from "@/lib/listingHealth";

/** Home: a small card while some connected shop's sales were never read; it links to Listing health's "Read sales". */
export function SalesNotReadCard() {
  const [sales, setSales] = useState<HealthSales | null>(null);
  useEffect(() => {
    let live = true;
    api.healthSales().then((s) => { if (live) setSales(s); }).catch(() => undefined);
    return () => { live = false; };
  }, []);
  const card = homeSalesCard(sales);
  if (!card) return null;
  return (
    <p className="card flex flex-wrap items-center justify-between gap-3 p-3 text-sm text-slate-700" data-testid="home-sales-not-read">
      <span translate="no">{card.text}</span>
      <Link href={card.href} className="btn-secondary"><span translate="no">{card.link}</span></Link>
    </p>
  );
}
