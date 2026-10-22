import { StatusChip, type Tone } from "./StatusChip";

/* An upload or batch status word, in the one status chip every page uses (B9). */
const TONE: Record<string, Tone> = {
  uploaded: "neutral",
  processing: "attention",
  processed: "done",
  ready: "done",
  applied: "working",
  failed: "failed",
  uploading: "attention",
};

export function StatusPill({ status }: { status: string }) {
  return <StatusChip tone={TONE[status] ?? "neutral"}>{status}</StatusChip>;
}
