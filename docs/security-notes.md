# Security notes

Decisions about published advisories that touch our dependencies: what the advisory says, whether it
reaches our setup, and what we did. Newest first.

## Next.js 15.5.27: two advisories (9 November 2026)

`npm audit --omit=dev` reported two advisories against `next@15.5.27`, both published 22 October 2026
and both fixed in **15.5.28** (published 14 October 2026; 16.4.1 on the 16.x line):

| Advisory | Severity | What it needs | Our setup | Reaches us? |
|---|---|---|---|---|
| [GHSA-2464-fhr4-w9xw](https://github.com/advisories/GHSA-2464-fhr4-w9xw): RCE in the Image Optimization API when SVG files are used | critical | A memory bug in the image library behind `/_next/image`, "when Next.js runs on **glibc-based Linux**"; the advisory's workaround is `sharp` ≥ 0.35.5 | The production image is `node:20-alpine` (`docker/frontend.prod.Dockerfile`): **musl, not glibc**. We already install `sharp` **0.35.5** (`package.json`, the version Next resolves too). No page uses `next/image`, though the optimizer route exists by default. | **No** |
| [GHSA-p3m5-59mp-jv5r](https://github.com/advisories/GHSA-p3m5-59mp-jv5r): DoS in the App Router using Server Actions | high | An App Router app with **at least one Server Action** | **No Server Action anywhere** (no `"use server"` in `app/`, `components/`, `lib/`). The app's writes go to the FastAPI API; Next serves pages, the middleware and two route handlers (`app/og/[slug]`, the manifest). | **No** |

The rest of our setup does not change either answer: the build is a self-hosted standalone output behind
the Cloudflare Tunnel; `middleware.ts` only checks the session cookie on `/` against our own API; route
handlers draw share cards at build time.

**Decision:** neither advisory reaches us, so no exception to the two-week rule was needed. The fixed
release, 15.5.28, is already older than two weeks, so we upgraded to it under the normal rule (not to
15.5.29, which was 5 days old). Checks: `npm audit --omit=dev` finds 0 vulnerabilities; `npm ci` from
the lockfile succeeds; `npm audit signatures`: 145 packages have verified registry signatures and 60
verified attestations; `next` resolves to `https://registry.npmjs.org/next/-/next-15.5.28.tgz` with the
lockfile's sha512 integrity; `sharp` 0.35.5 and the `postcss` 8.5.26 override are unchanged. Every suite
was run after the upgrade (report of fix/onboarding-followups).
