/** Pure logic for the review sample-size field, kept out of the component so it can be tested.
 *
 * The field holds a string rather than a number: a controlled numeric input that coerces on every
 * keystroke is what produced the "0100" bug, because the typed digits were appended to a value the
 * component had not yet resolved. Here the raw text is normalised once, then interpreted.
 */

export type SampleUnit = "count" | "percent";

/** Largest value the field accepts for a unit. */
export const maxFor = (unit: SampleUnit, total: number): number =>
  unit === "count" ? Math.max(1, total) : 100;

/**
 * Normalise typed text: digits only, no leading zeros, clamped to the unit's range.
 *
 * @returns The text to display. An empty string is allowed so the field can be cleared.
 */
export function normaliseSize(next: string, unit: SampleUnit, total: number): string {
  const digits = next.replace(/[^0-9]/g, "").replace(/^0+(?=\d)/, "");
  if (digits === "") return "";
  return String(Math.min(Number.parseInt(digits, 10), maxFor(unit, total)));
}

export interface Sample {
  /** True when the field holds a usable number. */
  valid: boolean;
  /** Posts the sample resolves to, in both readings, always against the dataset total. */
  posts: number;
  percent: number;
}

/** Interpret the field's text as a post count and a percent of `total`. */
export function resolveSample(raw: string, unit: SampleUnit, total: number): Sample {
  const typed = Number.parseInt(raw, 10);
  const valid = Number.isFinite(typed) && typed >= 1 && typed <= maxFor(unit, total);
  if (!valid) return { valid: false, posts: 0, percent: 0 };
  const posts =
    unit === "count" ? Math.min(typed, total) : Math.max(1, Math.round((total * typed) / 100));
  return { valid: true, posts, percent: total === 0 ? 0 : Math.round((posts / total) * 100) };
}

/** Value to show after switching unit, preserving the chosen number of posts. */
export function convertOnUnitSwitch(sample: Sample, next: SampleUnit): string {
  if (!sample.valid) return "";
  return String(next === "percent" ? Math.max(1, sample.percent) : sample.posts);
}
