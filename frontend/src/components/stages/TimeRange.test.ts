import { describe, expect, it } from "vitest";

import { DEFAULT_RANGE, rangeError, toWindow, type TimeRange } from "./TimeRange";

const custom = (from: string, to: string): TimeRange => ({ preset: "custom", from, to });
const iso = (daysAgo: number) =>
  new Date(Date.now() - daysAgo * 86_400_000).toISOString().slice(0, 10);

describe("toWindow", () => {
  it("sends only a start for a preset, leaving the end open at now", () => {
    const { start, end } = toWindow({ ...DEFAULT_RANGE, preset: "24h" });
    expect(end).toBeUndefined();
    const hours = (Date.now() - Date.parse(start!)) / 3_600_000;
    expect(hours).toBeGreaterThan(23.9);
    expect(hours).toBeLessThan(24.1);
  });

  it("covers whole UTC days, so the last day of a range is included", () => {
    // A range ending on the 13th must include posts made during the 13th, not stop at midnight.
    expect(toWindow(custom("2026-09-09", "2026-09-13"))).toEqual({
      start: "2026-09-09T00:00:00Z",
      end: "2026-09-13T23:59:59Z",
    });
  });
});

describe("rangeError", () => {
  it("accepts a complete range inside the seven-day window", () => {
    expect(rangeError(custom(iso(3), iso(0)))).toBeNull();
  });

  it("rejects a reversed range", () => {
    expect(rangeError(custom(iso(0), iso(3)))).toMatch(/before/);
  });

  it("rejects a range that reaches past what recent search serves", () => {
    expect(rangeError(custom(iso(30), iso(0)))).toMatch(/7 days/);
  });

  it("rejects an incomplete range rather than guessing the missing end", () => {
    expect(rangeError(custom(iso(3), ""))).toMatch(/both/);
  });

  it("never complains about a preset", () => {
    expect(rangeError(DEFAULT_RANGE)).toBeNull();
  });
});
