/**
 * A listing's photos in one shop (backend api/shop_photos.py): the order there, its first the cover, and the photos
 * left out there. The server decides and checks; these are the moves the editor offers before it saves.
 */

export type ShopPhotos = {
  content_id: string;
  shop_id: string;
  shop_name: string;
  photos: { id: string; filename: string }[];
  order: string[];
  out: string[];
  own: boolean;
  charts: number;
  room: number;
  note: string | null;
};

export type PhotoBulk = {
  rows: { content_id: string; label: string; before: string[]; after: string[]; changed: boolean; reason: string | null; note: string | null }[];
  changed: number;
  applied: boolean;
  sentence: string;
};

/** ``order`` with ``id`` moved by ``by`` places (−1 earlier, +1 later), staying inside the list. */
export function nudge(order: string[], id: string, by: number): string[] {
  const at = order.indexOf(id);
  const to = Math.max(0, Math.min(order.length - 1, at + by));
  if (at < 0 || to === at) return order;
  const out = order.filter((x) => x !== id);
  out.splice(to, 0, id);
  return out;
}

/** ``id`` dropped before ``target`` (a drag). */
export function dropBefore(order: string[], id: string, target: string): string[] {
  if (id === target || !order.includes(id) || !order.includes(target)) return order;
  const out = order.filter((x) => x !== id);
  out.splice(out.indexOf(target), 0, id);
  return out;
}

export const makeCover = (order: string[], id: string): string[] => (order.includes(id) ? [id, ...order.filter((x) => x !== id)] : order);

/** Leave a photo out in this shop; the last one stays (a listing needs a cover). */
export function leaveOut(order: string[], out: string[], id: string): { order: string[]; out: string[] } {
  if (order.length <= 1 || !order.includes(id)) return { order, out };
  return { order: order.filter((x) => x !== id), out: [...out, id] };
}

export const putBack = (order: string[], out: string[], id: string): { order: string[]; out: string[] } =>
  ({ order: [...order, id], out: out.filter((x) => x !== id) });

/** Said under the editor: Etsy's limit with this listing's size charts. */
export function limitLine(kept: number, room: number, charts: number): string {
  const chartWords = charts ? ` beside its ${charts} size chart${charts === 1 ? "" : "s"}` : "";
  if (kept > room) return `Too many: Etsy keeps 20 images, so ${room} photos fit${chartWords}. Leave ${kept - room} more out.`;
  return `${kept} of ${room} photos${chartWords}. Size charts keep their places.`;
}

/** The shop's own cover for a listing, when that shop has an order of its own (the cards show it). */
export function shopCover(listings: { content_id: string; photos?: Record<string, string[]> }[] | undefined, contentId: string, shopId: string | null | undefined): string | null {
  if (!shopId) return null;
  const order = listings?.find((l) => l.content_id === contentId)?.photos?.[shopId];
  return order?.length ? order[0] : null;
}
