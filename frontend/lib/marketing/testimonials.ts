/**
 * What sellers have said, for the home page. Only real, attributable words a seller agreed to have
 * shown; nothing is ever written here on anyone's behalf. Empty means the section is not shown.
 */

export type Testimonial = { quote: string; name: string; shop?: string };

export const TESTIMONIALS: Testimonial[] = [];
