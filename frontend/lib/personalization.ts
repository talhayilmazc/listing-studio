// Personalization limits (v8 §D). Etsy's personalization endpoint takes one
// text question of 1-45 characters (from Etsy's API reference); the API
// reference states no limit for instructions or the character limit, so the
// app keeps the limits it already enforced (backend pipeline/personalization.py).
import type { Personalization } from "./types";

export const MAX_QUESTION = 45;
export const MAX_INSTRUCTIONS = 256;
export const MAX_CHARACTERS = 1024;
export const DEFAULT_QUESTION = "Personalization";

/** Why a setting cannot be sent, or [] when it can. */
export function personalizationErrors(p: Personalization): string[] {
  if (!p.enabled) return [];
  const out: string[] = [];
  const q = (p.question_text ?? "").trim();
  if (q.length > MAX_QUESTION) out.push(`The question is limited to ${MAX_QUESTION} characters.`);
  if ((p.instructions ?? "").trim().length > MAX_INSTRUCTIONS) out.push(`Instructions are limited to ${MAX_INSTRUCTIONS} characters.`);
  const max = p.max_allowed_characters;
  if (max != null && (!Number.isInteger(max) || max < 1 || max > MAX_CHARACTERS))
    out.push(`The character limit must be between 1 and ${MAX_CHARACTERS}.`);
  return out;
}
