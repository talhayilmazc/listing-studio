"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { browserZone, zoneAbbreviation } from "@/lib/schedule";
import { useSession } from "./SessionProvider";

/**
 * Which zone a time is entered in, next to every schedule input. When this
 * computer's clock is set to another zone, says so: "5 PM" means the account's
 * 5 PM, not the laptop's, and a seller travelling (or with a mis-set clock)
 * should see that before saving.
 */
export function ZoneNote({ compact = false }: { compact?: boolean }) {
  const { timeZone } = useSession();
  // Read after mount: the server render has no idea of this computer's zone.
  const [here, setHere] = useState<string | null>(null);
  useEffect(() => setHere(browserZone()), []);
  const abbr = zoneAbbreviation(new Date(), timeZone);
  const differs = here !== null && here !== timeZone;

  return (
    <span translate="no" className="text-xs text-slate-500">
      <span>
        {compact ? `${abbr}` : `Times in ${timeZone.replace(/_/g, " ")} (${abbr})`}
      </span>
      {differs && (
        <span key="differs" className="text-amber-800">
          {" "}
          <span>{`· this computer is set to ${here!.replace(/_/g, " ")}`}</span>{" "}
          <Link href="/settings" className="underline">
            change
          </Link>
        </span>
      )}
    </span>
  );
}
