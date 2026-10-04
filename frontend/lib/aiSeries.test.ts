// Run with: npm test (node's built-in runner; Node 22.6+ strips the types).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  NO_SELLER_COLOR, OTHER_COLOR, SELLER_COLORS, axisLabels, axisTicks, csvName, figure, layers, sellerColor, seriesCsv, share,
  signed, stamp, usd, usdAxis,
} from "./aiSeries.ts";
import type { AiCell, AiSeries, AiSeriesSeller } from "./types.ts";

function cell(cost: number, listings = 0, calls = 1, failed = 0): AiCell {
  return {
    calls: cost === 0 && listings === 0 ? 0 : calls, failed, listings, cost_usd: cost.toFixed(6), unpriced: false,
    cost_per_listing_usd: listings ? (cost / listings).toFixed(6) : null,
  };
}

function seller(id: string, email: string | null, slot: number | null, costs: number[], shareOf = "0.5000"): AiSeriesSeller {
  return { id, email, slot, cells: costs.map((c) => cell(c, c > 0 ? 1 : 0)), total: cell(costs.reduce((a, b) => a + b, 0), costs.filter((c) => c > 0).length), share: shareOf };
}

function series(sellers: AiSeriesSeller[], over: Partial<AiSeries> = {}): AiSeries {
  const days = ["2026-10-03", "2026-10-04"];
  const sum = (i: number) => sellers.reduce((a, s) => a + Number(s.cells[i].cost_usd), 0);
  return {
    period: "daily", seller: "all", time_zone: "Europe/Istanbul", as_of: "2026-10-04T13:30:00Z",
    start: "2026-10-03T00:00:00+03:00", end: "2026-10-05T00:00:00+03:00",
    buckets: days.map((d, i) => ({
      start: `${d}T00:00:00+03:00`, end: `${days[i + 1] ?? "2026-10-05"}T00:00:00+03:00`, label: `${Number(d.slice(8))} Oct`,
      title: `${i ? "Sun" : "Sat"} ${Number(d.slice(8))} Oct 2026`, partial: i === 1, utc_day: d, utc: cell(0.01 * (i + 1), 1, 2), total: cell(sum(i), 1, 2),
    })),
    sellers, total: cell(sum(0) + sum(1), 3, 4, 1), all_sellers: cell(sum(0) + sum(1), 3, 4, 1), previous: cell(0.01, 1, 2),
    previous_start: "2026-10-01T00:00:00+03:00", previous_end: "2026-10-02T16:30:00+03:00", previous_covered: true,
    records_from: "2026-08-26", change: { cost: "100.0", listings: null, cost_per_listing: "-12.5" }, options: [], ...over,
  };
}

test("a seller's colour is their slot's, whoever else is on screen", () => {
  const a = seller("a", "a@example.com", 0, [0.01, 0.02]);
  const c = seller("c", "c@example.com", 2, [0.03, 0]);
  assert.equal(sellerColor(c), SELLER_COLORS[2]);
  const both = layers(series([c, a]));
  const alone = layers(series([c]));
  assert.deepEqual(both.map((l) => [l.key, l.color]), [["a", SELLER_COLORS[0]], ["c", SELLER_COLORS[2]]]);
  assert.deepEqual(alone.map((l) => [l.key, l.color]), [["c", SELLER_COLORS[2]]]); // filtering does not repaint
  assert.deepEqual(both[1].values, [0.03, 0]);
});

test("sellers past the eighth are drawn together; calls with no seller last, in their own grey", () => {
  const rows = [
    seller("none", null, null, [0.001, 0]),
    seller("x", "x@example.com", null, [0.01, 0.01]),
    seller("y", "y@example.com", null, [0.02, 0]),
    seller("a", "a@example.com", 0, [0.05, 0.05]),
  ];
  const out = layers(series(rows));
  assert.deepEqual(out.map((l) => l.key), ["a", "other", "none"]);
  assert.equal(out[1].name, "2 other sellers");
  assert.equal(out[1].color, OTHER_COLOR);
  assert.deepEqual(out[1].values.map((v) => v.toFixed(2)), ["0.03", "0.01"]);
  assert.equal(out[2].color, NO_SELLER_COLOR);
  assert.equal(out[2].name, "No seller (our own runs)");
  assert.equal(new Set([...SELLER_COLORS, OTHER_COLOR, NO_SELLER_COLOR]).size, 10);
});

test("the axis has clean steps that reach past the largest bar", () => {
  assert.deepEqual(axisTicks(0), [0]);
  assert.deepEqual(axisTicks(0.083), [0, 0.05, 0.1]);
  assert.deepEqual(axisTicks(0.0314), [0, 0.02, 0.04]);
  assert.deepEqual(axisTicks(7.2), [0, 2.5, 5, 7.5]);
  assert.deepEqual(axisTicks(300), [0, 100, 200, 300]);
  const t = axisTicks(0.0314);
  assert.deepEqual(t.map((v) => usdAxis(v, t[1])), ["$0", "$0.02", "$0.04"]);
  assert.deepEqual(axisTicks(7.2).map((v) => usdAxis(v, 2.5)), ["$0", "$2.50", "$5.00", "$7.50"]);
  assert.deepEqual(axisTicks(300).map((v) => usdAxis(v, 100)), ["$0", "$100", "$200", "$300"]);
  assert.equal(usdAxis(0.005, 0.0025), "$0.0050");
});

test("figures read the same in the cards, the table and the readout", () => {
  assert.equal(usd("0.031370"), "$0.0314");
  assert.equal(usd("12.5"), "$12.50");
  assert.equal(usd("1234.5"), "$1,234.50");
  assert.equal(usd("0"), "$0.00");
  assert.equal(usd(null), "—");
  assert.equal(signed("48.8"), "+48.8%");
  assert.equal(signed("-12.5"), "−12.5%");
  assert.equal(signed("0.0"), "0.0%");
  assert.equal(signed(null), null);
  assert.equal(share("0.4219"), "42.2%");
  assert.equal(share(null), "—");
  assert.equal(stamp("2026-10-03T16:30:00+03:00"), "3 Oct 16:30");
  assert.equal(stamp("2026-10-03T16:30:00+03:00", false), "3 Oct 2026");
  assert.deepEqual(figure(cell(0.0123, 2), "cost"), { text: "$0.0123", zero: false });
  assert.deepEqual(figure(cell(0), "cost"), { text: "$0.0000", zero: true });
  assert.deepEqual(figure({ ...cell(0), calls: 3, unpriced: true }, "cost"), { text: "$0.0000+", zero: false });
  assert.deepEqual(figure(cell(0.01), "per_listing"), { text: "—", zero: true });
  assert.deepEqual(figure(cell(0.01, 2, 5, 1), "failed"), { text: "1", zero: false });
});

test("axis labels: hours on the clock, the rest counted back from the newest", () => {
  const hours = Array.from({ length: 24 }, (_, i) => {
    const h = (17 + i) % 24;
    return { start: `2026-10-0${h >= 17 ? 3 : 4}T${String(h).padStart(2, "0")}:00:00+03:00` };
  });
  const s = series([], { period: "24h", buckets: hours as AiSeries["buckets"] });
  const { wide, narrow } = axisLabels(s);
  assert.deepEqual(hours.filter((_, i) => wide[i]).map((b) => b.start.slice(11, 13)), ["18", "21", "00", "03", "06", "09", "12", "15"]);
  assert.deepEqual(hours.filter((_, i) => narrow[i]).map((b) => b.start.slice(11, 13)), ["18", "00", "06", "12"]);
  const daily = axisLabels(series([seller("a", "a@example.com", 0, [0.01, 0.02])]));
  assert.deepEqual(daily.wide, [false, true]); // the newest always carries one
});

test("the export is what is on screen: every seller in every bucket, totals, share, the UTC day", () => {
  const rows = [seller("a", "a@example.com", 0, [0.01, 0.02], "0.7500"), seller("none", null, null, [0, 0.01], "0.2500")];
  const text = seriesCsv(series(rows));
  const lines = text.trimEnd().split("\r\n");
  assert.equal(lines[0], "period,starts_istanbul,ends_istanbul,seller,cost_usd,listings,cost_per_listing_usd,calls,failures,share_of_all_sellers_cost,utc_day,utc_day_cost_usd,utc_day_calls");
  assert.equal(lines.length, 1 + 2 * 3 + 2 + 1 + 1);
  assert.equal(lines[1], "Sat 3 Oct 2026,2026-10-03 00:00,2026-10-04 00:00,a@example.com,0.010000,1,0.010000,1,0,,,,");
  assert.equal(lines[3], "Sat 3 Oct 2026,2026-10-03 00:00,2026-10-04 00:00,All sellers shown,0.010000,1,0.010000,2,0,,2026-10-03,0.010000,2");
  assert.equal(lines[7], "Total,2026-10-03 00:00,2026-10-05 00:00,a@example.com,0.030000,2,0.015000,1,0,0.7500,,,");
  assert.equal(lines[8], "Total,2026-10-03 00:00,2026-10-05 00:00,No seller (our own runs),0.010000,1,0.010000,1,0,0.2500,,,");
  assert.equal(lines[9], "Total,2026-10-03 00:00,2026-10-05 00:00,All sellers shown,0.040000,3,0.013333,4,1,1.0000,,,");
  assert.equal(lines[10], "Previous period,2026-10-01 00:00,2026-10-02 16:30,All sellers shown,0.010000,1,0.010000,2,0,,,,");
  assert.match(seriesCsv(series(rows, { previous_covered: false })), /Previous period \(not fully recorded\)/);
  assert.equal(csvName(series(rows)), "ai-cost-daily-all-sellers-2026-10-04.csv");
  assert.equal(csvName(series(rows, { seller: "none", as_of: "2026-10-04T22:30:00Z" })), "ai-cost-daily-no-seller-2026-10-05.csv");
});

test("an address that would run as a formula is exported as text", () => {
  const text = seriesCsv(series([seller("e", '=cmd|"x"@example.com', 0, [0.01, 0])]));
  assert.ok(text.includes(`,"'=cmd|""x""@example.com",`));
  assert.ok(!/,=cmd/.test(text));
});
