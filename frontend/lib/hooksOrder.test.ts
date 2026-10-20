// A React hook must never come after an early return in a component: the render that returns
// early calls fewer hooks than the next one and React throws ("Rendered more hooks than during
// the previous render"), taking the whole page down. It happened on Overview once a seller had
// unfinished drafts. A plain scan of every component: inside a top-level function, a hook call at
// the function's own indentation after a line that returns at that indentation.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";

const ROOT = path.resolve(import.meta.dirname, "..");

function files(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) return e.name === "node_modules" ? [] : files(p);
    return p.endsWith(".tsx") ? [p] : [];
  });
}

const HOOK = /^ {2}(const|let)\s.*=\s*use[A-Z]\w*(<.*>)?\(|^ {2}use[A-Z]\w*(<.*>)?\(/;

export function offenders(source: string): string[] {
  const out: string[] = [];
  let inFn: string | null = null;
  let returned = false;
  for (const line of source.split(/\r?\n/)) {
    const start = /^(export (default )?)?function ([A-Z]\w*)\(/.exec(line);
    if (start) {
      inFn = start[3];
      returned = false;
      continue;
    }
    if (line.startsWith("}")) {
      inFn = null;
      continue;
    }
    if (!inFn) continue;
    if (/^ {2}if \(.*\) return\b/.test(line)) returned = true;
    else if (returned && HOOK.test(line)) out.push(`${inFn}: ${line.trim()}`);
  }
  return out;
}

test("no component calls a hook after an early return", () => {
  const found: string[] = [];
  for (const file of [...files(path.join(ROOT, "app")), ...files(path.join(ROOT, "components"))]) {
    for (const o of offenders(fs.readFileSync(file, "utf8"))) found.push(`${path.relative(ROOT, file)}: ${o}`);
  }
  assert.deepEqual(found, []);
});

test("the scan finds the bug it is for", () => {
  const bad = "export function A() {\n  const [a] = useState(1);\n  if (!a) return null;\n  const [b] = useState(2);\n  return b;\n}\n";
  assert.deepEqual(offenders(bad), ["A: const [b] = useState(2);"]);
  assert.deepEqual(offenders(bad.replace("  if (!a) return null;\n", "")), []);
});
