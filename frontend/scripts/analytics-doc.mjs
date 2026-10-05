// Writes docs/analytics.md from lib/analyticsDefinitions.ts (the tooltips' own text).
// Run from frontend/: node scripts/analytics-doc.mjs
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { document } from "../lib/analyticsDefinitions.ts";

const target = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "docs", "analytics.md");
fs.writeFileSync(target, document(), "utf8");
console.log("wrote", target);
