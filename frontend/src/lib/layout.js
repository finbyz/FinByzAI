// Geometry, in one place.
//
// frappe-ui's page anatomy: a prose column is `max-w-[770px]`, gutters are
// `px-3 sm:px-5` and the same pair applies to the header, the body and any
// full-bleed row, so everything lines up down the panel. The composer, the message
// list and the empty state all import from here rather than repeating a width — a
// mismatch is what made the composer run edge-to-edge in fullscreen.

/** The reading column: centred, capped at prose width. */
export const COLUMN = "mx-auto w-full max-w-[770px]";

/** Horizontal gutters, identical on every row of the panel. */
export const GUTTER = "px-3 sm:px-5";

/** A scroll region ends with air, never flush against the next surface. */
export const SCROLL_END = "pb-10";
