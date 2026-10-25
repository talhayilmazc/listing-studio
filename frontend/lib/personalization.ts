// Personalization limits (v8 §D), as Etsy's Open API v3 and its personalization
// migration guide state them (backend pipeline/personalization.py, the same rules):
// one text question of 1-45 characters, instructions of at most 120, a character
// limit of 1-1024 (required: 256 is sent when none is set), and the question and
// instructions start with a letter or a number with at most one word in capitals.
import type { Personalization } from "./types";

export const MAX_QUESTION = 45;
export const MAX_INSTRUCTIONS = 120;
export const MAX_CHARACTERS = 1024;
export const DEFAULT_MAX_CHARACTERS = 256;
const SHOUTED = /\b[A-Z]{3,}/g;
/** A whole word Etsy counts as in capitals (it starts with three or more capital letters). */
const SHOUTED_WORD = /\b[A-Z]{3,}[\w'’]*/g;
export const DEFAULT_QUESTION = "Personalization";

/**
 * The one line on a review card (B5): where the listing's personalization comes from and whether
 * it is on. "Personalization: from its profile (on)", "Personalization: this listing's own (off)".
 */
export function personalizationLine(value: Personalization | null, source: string): string {
  if (source === "unknown" || (source !== "listing" && !value)) {
    return "Personalization: from its profile (read when its reference is read)";
  }
  const where = source === "listing" ? "this listing's own" : "from its profile";
  const state = value?.enabled ? (value.required ? "on, required" : "on") : "off";
  return `Personalization: ${where} (${state})`;
}

/** Why a setting cannot be sent, or [] when it can. */
export function personalizationErrors(p: Personalization): string[] {
  if (!p.enabled) return [];
  const out: string[] = [];
  const q = (p.question_text ?? "").trim();
  if (q.length > MAX_QUESTION) out.push(`The question is limited to ${MAX_QUESTION} characters.`);
  const ins = (p.instructions ?? "").trim();
  if (ins.length > MAX_INSTRUCTIONS) out.push(`Instructions are limited to ${MAX_INSTRUCTIONS} characters.`);
  for (const [name, value] of [["question", q], ["instructions", ins]] as const) {
    if (value && !/^[\p{L}\p{N}]/u.test(value)) out.push(`The ${name} must start with a letter or a number.`);
    if ((value.match(SHOUTED) ?? []).length > 1) out.push(`The ${name} may have only one word in capitals.`);
  }
  const max = p.max_allowed_characters;
  if (max != null && (!Number.isInteger(max) || max < 1 || max > MAX_CHARACTERS))
    out.push(`The character limit must be between 1 and ${MAX_CHARACTERS}.`);
  return out;
}

/** The words Etsy counts as in capitals (backend personalization.shouted). */
export function shouted(value: string): string[] {
  return value.match(SHOUTED_WORD) ?? [];
}

/**
 * The same text with the first word in capitals kept and every later one in normal case; a
 * word that starts a sentence keeps its first capital (backend personalization.fix_caps; both
 * read backend/tests/fixtures/personalization_caps_cases.json).
 */
export function fixCaps(value: string): string {
  let seen = false;
  return value.replace(SHOUTED_WORD, (word: string, offset: number) => {
    if (!seen) {
      seen = true;
      return word;
    }
    const before = value.slice(0, offset).trimEnd();
    const startsSentence = before === "" || ".!?:".includes(before[before.length - 1]);
    return startsSentence ? word[0] + word.slice(1).toLowerCase() : word.toLowerCase();
  });
}

/** The live counter under a personalization field: "143 / 120 characters", and whether it is over. */
export function counter(text: string | null | undefined, limit: number): { line: string; over: boolean } {
  const n = (text ?? "").trim().length;
  return { line: `${n} / ${limit} characters`, over: n > limit };
}
