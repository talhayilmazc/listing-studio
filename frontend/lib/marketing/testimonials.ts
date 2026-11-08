/**
 * What sellers have said, for the home page's yellow section. Only real customers' own words, each
 * approved by the person for this use, word for word (owner, 8 November 2026). Nothing is ever written
 * here on anyone's behalf, edited or added to. The first is shown large and the others as cards under it;
 * the avatar shows the initials. Empty means the section shows our promise instead (PROMISE).
 */
import { TRIAL } from "../plans.ts";

export type Testimonial = { quote: string; name: string; about: string };

export const TESTIMONIALS: Testimonial[] = [
  {
    quote:
      "Listyro turns hours of work into a few minutes. I go from mockups all the way to publishing without leaving one app. And the scheduler is a lifesaver: I was by the pool on vacation while my listings went live on their own.",
    name: "James D.",
    about: "Runs 17 shops on Listyro",
  },
  {
    quote:
      "I was spending so many hours on Etsy for some extra income that my kids sometimes got less of me. Now I get it done in a fraction of the time. Thank you, Listyro.",
    name: "Mary C.",
    about: "Mom of two, Etsy seller",
  },
  {
    quote:
      "I've put years of work into my Etsy shop. If I'd had Listyro when I started, I'd have gotten here much faster. It covers everything I need, and when I asked for a new feature, they added it fast.",
    name: "Michael R.",
    about: "Selling on Etsy for 12 years",
  },
];

/** "James D." -> "JD". */
export const initials = (name: string) =>
  name
    .split(/\s+/)
    .map((part) => part.replace(/[^A-Za-z]/g, "").charAt(0))
    .join("")
    .toUpperCase();

/** In the yellow section's place when there is no testimonial to show. */
export const PROMISE = {
  label: "Our promise",
  line: `Try it on ${TRIAL.listings} designs. If it doesn't save you time, walk away.`,
  badges: ["No card", "Nothing to cancel", "Keep every draft it made"],
};
