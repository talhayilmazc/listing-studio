"use client";

import { usePathname } from "next/navigation";
import { useEffect } from "react";
import { api } from "@/lib/api";
import { toast } from "@/lib/toast";

/**
 * "Listings ready" on whatever page the seller opens after the worker finished writing a batch
 * (workers/writing.py), even if the tab that started it was closed. The server keeps the note until it
 * is read once (``GET /api/writing/ready``), so the seller is told once.
 */
export const WRITING_DONE_EVENT = "listyro:writing-done";

/** The batch page saw its run finish: look now rather than at the next page. */
export function announceWritingDone(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(WRITING_DONE_EVENT));
}

export function WritingReadyWatcher() {
  // Looked again on each page the seller opens: one small read, cleared once told.
  const pathname = usePathname();
  useEffect(() => {
    let alive = true;
    const look = async () => {
      try {
        const notes = await api.writingReady();
        if (!alive) return;
        for (const n of notes) {
          if (n.written > 0) {
            toast(`Listings ready: ${n.name}`, { action: { label: "Review", href: `/batches/${n.batch_id}/review` } });
          } else if (n.failed > 0) {
            toast(`${n.failed} listing${n.failed === 1 ? "" : "s"} could not be written`, {
              tone: "error",
              action: { label: "See why", href: `/batches/${n.batch_id}` },
            });
          }
        }
      } catch {
        // signed out or offline: the note waits on the server for the next page
      }
    };
    look();
    window.addEventListener(WRITING_DONE_EVENT, look);
    return () => {
      alive = false;
      window.removeEventListener(WRITING_DONE_EVENT, look);
    };
  }, [pathname]);
  return null;
}
