// After a deploy: every public page answers, every link and anchor on them
// resolves, and every image the site serves (through the image optimizer, at
// every width it offers) comes back as an image.
//
//   node deploy/check-site.mjs https://listyro.com
const BASE = (process.argv[2] || "https://listyro.com").replace(/\/$/, "");
const PAGES = ["/", "/pricing", "/request-invite", "/contact", "/terms", "/privacy"];
const UA = { "user-agent": "Mozilla/5.0 (Listyro site check)", accept: "text/html,image/avif,image/webp,*/*" };
const problems = [];
let images = 0;
let links = 0;
for (const page of PAGES) {
  const r = await fetch(BASE + page, { headers: UA, redirect: "manual" });
  if (r.status !== 200) { problems.push(`${page}: ${r.status}`); continue; }
  const html = await r.text();
  const urls = new Set();
  for (const m of html.matchAll(/(?:src|srcSet|href)="([^"]+)"/g)) {
    for (const part of m[1].replace(/&amp;/g, "&").split(",")) {
      const u = part.trim().split(/\s+/)[0];
      if (u.startsWith("/") && !u.startsWith("//")) urls.add(u);
    }
  }
  for (const m of html.matchAll(/<meta[^>]+content="(https?:\/\/[^"]+\.(?:png|jpg|webp))"/g)) urls.add(new URL(m[1]).pathname);
  for (const u of urls) {
    const res = await fetch(BASE + u.split("#")[0], { headers: UA, redirect: "manual" });
    const type = res.headers.get("content-type") || "";
    if (u.startsWith("/_next/image") || /\.(png|webp|jpg|svg|ico)$/.test(u.split("?")[0])) {
      images++;
      if (res.status !== 200 || !/^image\//.test(type)) problems.push(`${page} image ${u}: ${res.status} ${type}`);
    } else {
      links++;
      if (res.status >= 400) problems.push(`${page} link ${u}: ${res.status}`);
    }
  }
}
console.log(`${PAGES.length} pages, ${links} links, ${images} image URLs checked against ${BASE}`);
console.log(problems.length ? "PROBLEMS:\n" + problems.join("\n") : "all good");
process.exit(problems.length ? 1 : 0);
