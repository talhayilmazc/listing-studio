// The structure behind three layout faults, checked on every `npm test`.
// (The pages themselves are measured in a browser by e2e/layout.spec.ts, which needs
// a running server: `npm run test:layout`.)
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";

const FRONTEND = path.join(import.meta.dirname, "..");
const read = (relative: string) => fs.readFileSync(path.join(FRONTEND, relative), "utf8").replace(/\r\n/g, "\n");

test("the sign-in shell's footer is the frame's last child, not part of the centred column", () => {
  const shell = read("components/AuthShell.tsx");
  // The centred column closes, then the footer, then the frame.
  assert.match(shell, /\{footer && <div[^>]*>\{footer\}<\/div>\}\n\s*<\/div>\n\s*<AuthFooter \/>\n\s*<\/div>\n\s*\);/);
  // The frame has no bottom padding of its own: the footer carries it, so the page ends with the footer.
  assert.match(shell, /<div className="flex min-h-screen flex-col bg-stone-50 px-6 pt-10">/);
  assert.match(shell, /<footer className="[^"]*\bpb-10\b[^"]*"/);
});

test("every page's frame puts its footer last", () => {
  assert.match(read("app/(marketing)/layout.tsx"), /<main id="main" className="flex-1">\{children\}<\/main>\n\s*<SiteFooter \/>\n\s*<\/div>/);
  assert.match(read("components/AppShell.tsx"), /<\/main>\n\s*<Footer \/>\n\s*<\/div>/);
});

test("the Admin tab bar keeps its shape: fixed slots for counts and the badge, panels mounted and hidden", () => {
  const admin = read("app/(app)/admin/page.tsx");
  // The slots are there from the first paint, whatever has loaded.
  assert.match(admin, /key="users-count" className="[^"]*\binline-block w-\[2\.5ch\]/);
  assert.match(admin, /key="invites-open" className="[^"]*\binline-block w-\[7\.5ch\]/);
  assert.match(admin, /key="pending-slot" className="[^"]*\binline-flex w-8\b[^"]*" data-testid="pending-requests-slot"/);
  assert.doesNotMatch(admin, /\{t\.id === "(users|invites)" && (users|invites) && \(/, "a count must not appear only once its data has loaded");
  // The bar scrolls inside itself if it is ever too wide; tabs do not wrap or shrink.
  assert.match(admin, /role="tablist"[^>]*className="flex gap-4 overflow-x-auto /);
  // Panels: all three in the page, switched with `hidden`.
  for (const tab of ["users", "invites", "usage"]) {
    assert.match(admin, new RegExp(`role="tabpanel" id="panel-${tab}" aria-labelledby="tab-${tab}" hidden=\\{tab !== "${tab}"\\}`));
  }
  assert.doesNotMatch(admin, /\{tab === "(users|invites|usage)" && /, "a panel mounted only when its tab is open jumps when it loads");
  assert.match(admin, /className="min-h-\[70vh\] pt-5" data-testid="admin-panels"/);
});

test("the Admin summary is laid out the same while loading as when loaded", () => {
  const usage = read("components/admin/Usage.tsx");
  const summary = usage.slice(usage.indexOf("export function UsageSummary"), usage.indexOf("function HistoryChart"));
  assert.doesNotMatch(summary, /if \(!usage\) \{\s*return/, "an early return with a shorter skeleton moves everything below it");
  assert.match(summary, /<div className=\{loading \? "invisible" : undefined\}>/);
  assert.match(summary, /\{loading && \(\s*<div key="skeleton" aria-hidden className="absolute inset-0 /);
  // The accounts that used the most: one line that is always there and never wraps.
  assert.match(summary, /<p className="mt-1 min-h-\[1\.3rem\] truncate [^"]*"[^>]*title=/);
});

test("long values wrap, wide tables scroll in their own container, and the scrollbar keeps its place", () => {
  assert.match(read("app/globals.css"), /html \{\n\s*scrollbar-gutter: stable;\n\}/);
  // The two lines that ran off the screen on the Admin Users tab.
  assert.doesNotMatch(read("components/admin/AllowanceControls.tsx"), /whitespace-nowrap/);
  const users = read("components/admin/UsersTab.tsx");
  assert.doesNotMatch(users, /<p[^>]*whitespace-nowrap/);
  // Every table of the Admin screens is inside a container that scrolls sideways by itself.
  for (const file of fs.readdirSync(path.join(FRONTEND, "components/admin")).filter((name) => name.endsWith(".tsx"))) {
    const source = read(`components/admin/${file}`);
    for (const match of source.matchAll(/<table\b/g)) {
      const before = source.slice(0, match.index);
      const container = Math.max(before.lastIndexOf("overflow-x-auto"), before.lastIndexOf("overflow-auto"));
      const small = /^<table className="[^"]*\bmax-w-sm\b/.test(source.slice(match.index!));
      // (A table drawn by a helper component sits in the container of the place that renders
      // it, earlier in the same file; the browser test measures the result.)
      assert.ok(small || container !== -1, `${file}: the table at line ${before.split("\n").length} has no scrolling container before it`);
    }
  }
});

test("the layout test in the browser can only be aimed at our own pages", () => {
  const config = read("playwright.config.ts");
  assert.match(config, /const OURS = new Set\(\["localhost", "127\.0\.0\.1", "listyro\.com", "www\.listyro\.com"\]\);/);
  assert.match(config, /if \(!OURS\.has\(new URL\(BASE\)\.hostname\)\) \{\s*throw new Error/);
  const spec = read("e2e/layout.spec.ts");
  assert.doesNotMatch(spec + config, /etsy\.com|etsystatic/i);
  assert.equal(JSON.parse(read("package.json")).scripts["test:layout"], "playwright test");
});
