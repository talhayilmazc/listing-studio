// What each mockup tile shows while the seller edits (seller feedback: the results update live
// while the design editor stays open). One source of truth: the run as the server returns it
// (`refreshing`, `refresh_failed`, each output's `stamp`), plus the pairs this page has just
// asked for and the server has not answered yet ("optimistic"). The results list and the
// editor's strip both read the same state, so whatever is open shows the latest.
import type { MockupOutput, MockupRun } from "./types";

export type TileState = { kind: "ready" } | { kind: "updating" } | { kind: "failed"; reason: string };

/** Pairs asked for on this page, with the stamp their tile had then; cleared by the server's answer. */
export type Optimistic = Record<string, string>;

export const pairKey = (designId: string, photoId: string) => `${designId}:${photoId}`;

type RunLike = Pick<MockupRun, "status" | "designs"> & Partial<Pick<MockupRun, "refreshing" | "refresh_failed">>;

/** A tile's state: being made again (in the server's queue or just asked for, and its version not
 * changed since: each tile swaps in as soon as its own new file is made), could not be made again
 * (with why), or ready. */
export function tileState(run: RunLike, designId: string, output: Pick<MockupOutput, "photo_id" | "stamp">, optimistic: Optimistic = {}): TileState {
  if (!output.photo_id) return { kind: "ready" };
  const key = pairKey(designId, output.photo_id);
  const stamp = output.stamp ?? "";
  const queued = (run.refreshing ?? []).find((p) => pairKey(p.design_id, p.photo_id) === key);
  if (queued && (queued.from_stamp ?? "") === stamp) return { kind: "updating" };
  if (key in optimistic && optimistic[key] === stamp) return { kind: "updating" };
  const failed = (run.refresh_failed ?? []).find((p) => pairKey(p.design_id, p.photo_id) === key);
  if (failed) return { kind: "failed", reason: failed.reason };
  return { kind: "ready" };
}

/** The page keeps asking for the run while it is being made or any mockup is being made again. */
export function keepPolling(run: RunLike | null | undefined): boolean {
  if (!run) return false;
  return !(run.status === "succeeded" || run.status === "failed") || (run.refreshing ?? []).length > 0;
}

/** The stamp each pair's tile has now: what "optimistic" remembers when the seller asks. */
export function stampsOf(run: RunLike, pairs: [string, string][]): Optimistic {
  const out: Optimistic = {};
  for (const [d, p] of pairs) {
    const o = run.designs.find((x) => x.design_id === d)?.outputs.find((x) => x.photo_id === p);
    if (o) out[pairKey(d, p)] = o.stamp ?? "";
  }
  return out;
}

/** The tiles the editor's strip shows: this design's mockups, then every other one touched since it
 * opened (so a change applied to other designs is seen landing, and stays seen). */
export function editorTiles(run: RunLike, designId: string, touched: string[]): { designId: string; output: MockupOutput }[] {
  const mine = run.designs.find((d) => d.design_id === designId)?.outputs ?? [];
  const others = run.designs.filter((d) => d.design_id !== designId).flatMap((d) =>
    d.outputs.filter((o) => o.photo_id && touched.includes(pairKey(d.design_id, o.photo_id))).map((o) => ({ designId: d.design_id, output: o })));
  return [...mine.map((o) => ({ designId, output: o })), ...others];
}

/** One line for the strip: how many are still being made, or that all are up to date. */
export function updatingLine(states: TileState[]): string {
  const updating = states.filter((s) => s.kind === "updating").length;
  const failed = states.filter((s) => s.kind === "failed").length;
  if (updating) return `Updating ${updating} ${updating === 1 ? "mockup" : "mockups"}…`;
  if (failed) return `${failed} ${failed === 1 ? "mockup" : "mockups"} could not be updated.`;
  return "Every mockup is up to date.";
}
