// Keeps page text safe from browser translators (Chrome's built-in one, extensions).
//
// A translator replaces text nodes with its own elements. React still holds the
// originals, so removing one, or inserting a sibling before one, throws
// ("Failed to execute 'removeChild' on 'Node'") and the page crashes; a value
// that only changes is written into a node no longer on the page, so it goes
// stale. This finds, in every child list of two or more nodes:
//   - dynamic text (a value) that isn't alone inside an element React owns;
//   - static text next to something that can appear, disappear or change kind;
//   - conditionally rendered elements (`cond && <X/>`) without a stable key.
// Whitespace-only text is left alone: translators skip it.
//
//   node scripts/translation-safety.cjs           check; exits 1 if anything is found
//   node scripts/translation-safety.cjs --apply   rewrite the files
//
// Text that can be empty is wrapped in <Txt> (components/Txt.tsx), which renders
// nothing when empty; always-present text in a <span>.
const ts = require(process.cwd() + "/node_modules/typescript");
const fs = require("fs");
const path = require("path");

const APPLY = process.argv.includes("--apply");
const cfg = ts.getParsedCommandLineOfConfigFile("tsconfig.json", {}, { ...ts.sys, onUnRecoverableConfigFileDiagnostic: () => {} });
const files = cfg.fileNames.filter((f) => f.endsWith(".tsx") && /[\\/](app|components)[\\/]/.test(f) && !f.endsWith("Txt.tsx"));
const program = ts.createProgram(files, cfg.options);
const checker = program.getTypeChecker();

const SKIP_PARENTS = new Set(["option", "title", "textarea", "style", "script", "text", "tspan", "svg", "Txt"]);

function unwrap(e) {
  while (e && ts.isParenthesizedExpression(e)) e = e.expression;
  return e;
}
function isJsx(e) {
  return ts.isJsxElement(e) || ts.isJsxSelfClosingElement(e) || ts.isJsxFragment(e);
}
function typeKinds(t, out) {
  if (t.isUnion()) { for (const u of t.types) typeKinds(u, out); return; }
  const f = t.flags;
  if (f & (ts.TypeFlags.StringLike | ts.TypeFlags.NumberLike | ts.TypeFlags.BigIntLike)) out.add("text");
  else if (f & (ts.TypeFlags.Null | ts.TypeFlags.Undefined | ts.TypeFlags.Void | ts.TypeFlags.BooleanLike | ts.TypeFlags.Never)) out.add("none");
  else if (f & (ts.TypeFlags.Any | ts.TypeFlags.Unknown)) out.add("opaque");
  else if (checker.isArrayType(t) || checker.isTupleType(t)) out.add("list");
  else {
    const name = checker.typeToString(t);
    if (/Element|ReactPortal|Iterable|Promise/.test(name)) out.add(/Iterable/.test(name) ? "opaque" : "el");
    else out.add("opaque");
  }
}
// Possible outcomes of a child expression: text, none, el, list, opaque.
function kinds(e) {
  e = unwrap(e);
  const out = new Set();
  if (!e) { out.add("none"); return out; }
  if (isJsx(e)) out.add("el");
  else if (e.kind === ts.SyntaxKind.NullKeyword || e.kind === ts.SyntaxKind.TrueKeyword || e.kind === ts.SyntaxKind.FalseKeyword ||
    (ts.isIdentifier(e) && e.text === "undefined")) out.add("none");
  // "" renders no text node at all, so it counts as nothing (not an empty span).
  else if ((ts.isStringLiteral(e) || ts.isNoSubstitutionTemplateLiteral(e)) && e.text === "") out.add("none");
  else if (ts.isStringLiteral(e) || ts.isNoSubstitutionTemplateLiteral(e) || ts.isNumericLiteral(e)) out.add("text");
  else if (ts.isConditionalExpression(e)) { for (const k of kinds(e.whenTrue)) out.add(k); for (const k of kinds(e.whenFalse)) out.add(k); }
  else if (ts.isBinaryExpression(e) && e.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken) { out.add("none"); for (const k of kinds(e.right)) out.add(k); }
  else if (ts.isBinaryExpression(e) && (e.operatorToken.kind === ts.SyntaxKind.BarBarToken || e.operatorToken.kind === ts.SyntaxKind.QuestionQuestionToken)) {
    for (const k of kinds(e.left)) out.add(k); for (const k of kinds(e.right)) out.add(k);
  } else if (ts.isCallExpression(e) && ts.isPropertyAccessExpression(e.expression) && e.expression.name.text === "map") out.add("list");
  else typeKinds(checker.getTypeAtLocation(e), out);
  return out;
}
function isStaticText(e) {
  e = unwrap(e);
  return e && (ts.isStringLiteral(e) || ts.isNoSubstitutionTemplateLiteral(e) || ts.isNumericLiteral(e));
}
function tagName(node) {
  const open = ts.isJsxElement(node) ? node.openingElement : null;
  return open ? open.tagName.getText() : "";
}
// JSX drops whitespace-only text that contains a line break.
function meaningful(child) {
  if (ts.isJsxText(child)) return !(child.text.trim() === "" && /\n/.test(child.text));
  if (ts.isJsxExpression(child)) return !!child.expression;
  return true;
}

const report = { files: 0, wrapText: 0, wrapExpr: 0, txt: 0, keys: 0, flagged: [] };

for (const file of files) {
  const sf = program.getSourceFile(file);
  const edits = [];
  let needsTxt = false;
  const rel = path.relative(process.cwd(), file).replace(/\\/g, "/");

  function wrapBranches(e) {
    // Rewrite text outcomes of a conditional in place, leaving element outcomes alone.
    e = unwrap(e);
    if (!e) return;
    if (ts.isConditionalExpression(e)) { wrapBranches(e.whenTrue); wrapBranches(e.whenFalse); return; }
    if (ts.isBinaryExpression(e) && e.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken) { wrapBranches(e.right); return; }
    const k = kinds(e);
    if (k.has("el") || k.has("list") || k.has("opaque")) {
      if (!isJsx(e) && !k.has("list")) report.flagged.push(`${rel}:${sf.getLineAndCharacterOfPosition(e.getStart()).line + 1} ${e.getText().slice(0, 70)}`);
      return;
    }
    if (!k.has("text")) return;
    if (k.has("none")) { edits.push([e.getStart(), e.getEnd(), `<Txt>{${e.getText()}}</Txt>`]); needsTxt = true; report.txt++; }
    else { edits.push([e.getStart(), e.getEnd(), `<span>{${e.getText()}}</span>`]); report.wrapExpr++; }
  }

  // Wrapper for one text expression: <Txt> when it can be empty, else <span>.
  function wrapExpr(c, k) {
    if (k.has("none")) { needsTxt = true; report.txt++; return `<Txt>${c.getText()}</Txt>`; }
    report.wrapExpr++;
    return `<span>${c.getText()}</span>`;
  }
  // A JsxText's range without the whitespace JSX drops (runs containing a line break).
  function textBounds(c, keepLead, keepTrail) {
    const text = c.getFullText();
    const start = c.getFullStart();
    let a = 0, b = text.length;
    while (a < b && /\s/.test(text[a])) a++;
    while (b > a && /\s/.test(text[b - 1])) b--;
    if (a === b) return null;
    if (keepLead || !/\n/.test(text.slice(0, a))) a = 0;
    if (keepTrail || !/\n/.test(text.slice(b))) b = text.length;
    return [start + a, start + b];
  }

  function visitChildren(parent, children) {
    const name = tagName(parent);
    if (SKIP_PARENTS.has(name)) return;
    const list = children.filter(meaningful);
    if (list.length < 2) return;
    const info = list.map((c) => {
      if (ts.isJsxText(c)) return { c, k: new Set(["text"]), stat: true, textish: true };
      if (ts.isJsxExpression(c)) {
        const k = kinds(c.expression);
        const textish = k.has("text") && !k.has("el") && !k.has("opaque") && !k.has("list");
        return { c, k, stat: isStaticText(c.expression), textish };
      }
      // <Txt> renders nothing when empty, so it can appear and disappear.
      if ((ts.isJsxElement(c) ? c.openingElement.tagName : ts.isJsxSelfClosingElement(c) ? c.tagName : null)?.getText() === "Txt") {
        return { c, k: new Set(["el", "none"]), stat: false, textish: false };
      }
      return { c, k: new Set(["el"]), stat: false, textish: false };
    });
    // Structure can change: something can appear, disappear or change kind.
    const mutable = info.some(({ k }) => k.size > 1 || k.has("list") || k.has("opaque"));

    // Adjacent text nodes render as one run (one anonymous box in a flex or grid
    // row), so each run is wrapped as a whole: one span keeps the layout as it was.
    const runs = [];
    let run = [];
    for (const x of info) {
      if (x.textish) { run.push(x); continue; }
      if (run.length) runs.push(run);
      run = [];
      const e = ts.isJsxExpression(x.c) ? unwrap(x.c.expression) : null;
      if (e && x.k.has("text")) wrapBranches(e);
      // Stable keys for elements that are conditionally rendered next to siblings.
      if (e && ts.isBinaryExpression(e) && e.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken) {
        const r = unwrap(e.right);
        const open = r && (ts.isJsxElement(r) ? r.openingElement : ts.isJsxSelfClosingElement(r) ? r : null);
        if (open && !open.attributes.properties.some((p) => p.name && p.name.getText() === "key")) {
          const at = open.tagName.getEnd();
          edits.push([at, at, ` key="${keyFor(open.tagName.getText(), x.c)}"`]);
          report.keys++;
        }
      }
    }
    if (run.length) runs.push(run);

    const blank = (x) => ts.isJsxText(x.c) ? x.c.text.trim() === "" : x.stat && unwrap(x.c.expression).getText().replace(/["'`]/g, "").trim() === "";
    for (const r of runs) {
      if (r.every(blank)) continue; // whitespace only: translators leave it alone
      const dyn = r.some((x) => !x.stat);
      if (!mutable && !dyn) continue; // static text in a list that never changes shape
      const cond = r.some((x) => x.k.has("none"));
      if (r.length === 1) {
        const x = r[0];
        if (ts.isJsxText(x.c)) {
          const [a, b] = textBounds(x.c, false, false);
          edits.push([a, b, `<span>${sf.getFullText().slice(a, b)}</span>`]);
          report.wrapText++;
        } else {
          edits.push([x.c.getStart(), x.c.getEnd(), x.stat ? `<span>${x.c.getText()}</span>` : wrapExpr(x.c, x.k)]);
          if (x.stat) report.wrapExpr++;
        }
        continue;
      }
      // Several nodes: one outer span; inside it each value gets its own element
      // (so it stays current), and when something inside can appear or disappear,
      // the static text next to it is wrapped too.
      if (r.every((x) => x.k.has("none") || blank(x))) {
        report.flagged.push(`${rel}:${sf.getLineAndCharacterOfPosition(r[0].c.getStart()).line + 1} text that can all be empty`);
      }
      const full = sf.getFullText();
      const first = r[0].c, last = r[r.length - 1].c;
      const a0 = ts.isJsxText(first) ? textBounds(first, false, true)?.[0] ?? first.getStart() : first.getStart();
      const b0 = ts.isJsxText(last) ? textBounds(last, true, false)?.[1] ?? last.getEnd() : last.getEnd();
      let out = "";
      let pos = a0;
      for (const x of r) {
        const xs = Math.max(ts.isJsxText(x.c) ? x.c.getFullStart() : x.c.getStart(), a0);
        const xe = Math.min(x.c.getEnd(), b0);
        out += full.slice(pos, xs);
        const body = full.slice(xs, xe);
        if (ts.isJsxText(x.c)) {
          out += cond && body.trim() ? body.replace(/^(\s*)([\s\S]*?)(\s*)$/, (m, l, t, tr) => `${/\n/.test(l) ? l : ""}<span>${/\n/.test(l) ? "" : l}${t}${/\n/.test(tr) ? "" : tr}</span>${/\n/.test(tr) ? tr : ""}`) : body;
          if (cond && body.trim()) report.wrapText++;
        } else if (x.stat) {
          out += cond && !blank(x) ? `<span>${body}</span>` : body;
        } else {
          out += wrapExpr(x.c, x.k);
        }
        pos = xe;
      }
      edits.push([a0, b0, `<span>${out}</span>`]);
      report.wrapText++;
    }
  }
  const used = new Map();
  function keyFor(tag, node) {
    const pos = sf.getLineAndCharacterOfPosition(node.getStart());
    return `${tag.replace(/\W/g, "").toLowerCase()}-${pos.line + 1}-${pos.character}`;
  }

  function visit(node) {
    if (ts.isJsxElement(node) || ts.isJsxFragment(node)) {
      const inSvg = ts.isJsxElement(node) && tagName(node) === "svg";
      if (!inSvg) visitChildren(node, [...node.children]);
      if (inSvg) return;
    }
    ts.forEachChild(node, visit);
  }
  visit(sf);
  if (!edits.length) continue;
  report.files++;
  // Nested edits (a branch inside an expression already wrapped) are dropped: outer wins.
  edits.sort((x, y) => x[0] - y[0] || y[1] - x[1]);
  const kept = [];
  for (const ed of edits) {
    const inside = kept.find((k) => ed[0] >= k[0] && ed[1] <= k[1] && !(ed[0] === ed[1] && k[0] === k[1]) && k[0] !== k[1]);
    if (inside) continue;
    kept.push(ed);
  }
  let src = sf.getFullText();
  for (const [a, b, rep] of kept.sort((x, y) => y[0] - x[0] || y[1] - x[1])) src = src.slice(0, a) + rep + src.slice(b);
  if (needsTxt && !/from "@\/components\/Txt"/.test(src)) {
    const imports = [...src.matchAll(/^import[\s\S]*?;\s*$/gm)];
    const last = imports[imports.length - 1];
    const at = last ? last.index + last[0].length : 0;
    src = src.slice(0, at) + '\nimport { Txt } from "@/components/Txt";' + src.slice(at);
  }
  if (APPLY) {
    fs.writeFileSync(file, src);
    console.log(`${rel}: ${kept.length} changes`);
  } else {
    for (const [a] of kept) console.log(`${rel}:${sf.getLineAndCharacterOfPosition(a).line + 1} not translation-safe`);
  }
}
for (const f of report.flagged) console.log(`note: ${f} (a node that can be text or an element; check by hand)`);
const found = report.wrapText + report.wrapExpr + report.txt + report.keys;
if (!APPLY && found) {
  console.log(`${found} place(s) to fix: run node scripts/translation-safety.cjs --apply`);
  process.exit(1);
}
