/**
 * Text that React fully owns, for text that can appear, disappear or change.
 *
 * Browser translators (Chrome's built-in one, extensions) replace page text
 * nodes with their own elements. React still holds the original text nodes, so
 * when it later removes one, or inserts a sibling before one, the DOM call
 * fails ("Failed to execute 'removeChild' on 'Node'") and the page crashes; a
 * value that merely changes is written into a node no longer on the page, so
 * the translated page shows a stale number. A text node alone inside an element
 * React owns is updated through that element instead, which survives both.
 *
 * Renders nothing for empty values, so a conditional string leaves no empty
 * span behind (which would add a gap in a flex row).
 */
export function Txt({ children }: { children?: string | number | boolean | null | undefined }) {
  if (children === null || children === undefined || children === false || children === true || children === "") {
    return null;
  }
  return <span>{children}</span>;
}
