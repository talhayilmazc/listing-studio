// Finding a profile among many (v7 §D1): by its name, its template, or the
// title of its reference listing. Every word typed must match somewhere.

export interface Searchable {
  name: string;
  content_template: string;
}

function fold(s: string): string {
  return s.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

export function matchesProfile(p: Searchable, query: string, referenceTitle?: string | null): boolean {
  const words = fold(query).split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const hay = fold(`${p.name} ${p.content_template.replace(/_/g, " ")} ${referenceTitle ?? ""}`);
  return words.every((w) => hay.includes(w));
}
