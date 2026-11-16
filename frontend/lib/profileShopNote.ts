/**
 * "Profile is from JPH; this listing goes to ILC" (16 November 2026). A listing goes where "Where and when"
 * says, whatever shop its profile's reference listing is in: the profile's shop never decides the
 * destination. The card says so when the two differ, and offers the destination shop's own profile when
 * that shop has exactly one of the same kind (two or more: the seller picks with the card's profile picker).
 */
type ProfileLike = { id: string; name: string; connection_id: string; shop_name: string | null; content_template: string };
type CellLike = { shop_id: string; shop_name: string };

export function profileNote(
  profiles: ProfileLike[], profileId: string | null, cells: CellLike[],
): { text: string; shop: string; own: { id: string; name: string } | null } | null {
  const profile = profiles.find((p) => p.id === profileId);
  if (!profile || cells.length === 0 || cells.some((c) => c.shop_id === profile.connection_id)) return null;
  const shops = cells.map((c) => c.shop_name).join(", ");
  const candidates = cells.length === 1
    ? profiles.filter((p) => p.connection_id === cells[0].shop_id && p.content_template === profile.content_template)
    : [];
  return {
    text: `Profile is from ${profile.shop_name ?? "another shop"}; this listing goes to ${shops}.`,
    shop: cells[0].shop_name,
    own: candidates.length === 1 ? { id: candidates[0].id, name: candidates[0].name } : null,
  };
}
