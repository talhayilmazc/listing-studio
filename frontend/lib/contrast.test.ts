// The colours text is written in read at WCAG AA (4.5:1) on every light surface, and the
// sidebar's text on the sidebar: checked from the tokens themselves on every `npm test` (the
// layout test, e2e/layout.spec.ts, measures the rendered pages).
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";

const ROOT = path.resolve(import.meta.dirname, "..");
const config = fs.readFileSync(path.join(ROOT, "tailwind.config.ts"), "utf8");
const css = fs.readFileSync(path.join(ROOT, "app", "globals.css"), "utf8");

const lum = (hex: string) => {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
export const ratio = (a: string, b: string) => {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};
const slate = (shade: number) => new RegExp(String.raw`\s${shade}: "(#[0-9a-f]{6})"`, "i").exec(config.slice(config.indexOf("slate:")))![1];
const token = (name: string) => new RegExp(String.raw`--${name}: (#[0-9a-f]{6})`, "i").exec(css)![1];

test("grey text reads at 4.5:1 or more on white, the page and the sunken grey", () => {
  for (const shade of [400, 500, 600, 700]) {
    for (const surface of ["#ffffff", token("bg"), token("surface-sunken")]) {
      assert.ok(ratio(slate(shade), surface) >= 4.5, `slate-${shade} on ${surface}: ${ratio(slate(shade), surface).toFixed(2)}`);
    }
  }
});

test("the sidebar's text and headings read on the sidebar", () => {
  assert.ok(ratio(token("rail-text"), token("rail")) >= 7, "rail text");
  assert.ok(ratio(token("rail-heading"), token("rail")) >= 4.5, "rail headings");
});
