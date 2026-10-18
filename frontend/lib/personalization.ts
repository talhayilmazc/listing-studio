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
export const DEFAULT_QUESTION = "Personalization";

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
