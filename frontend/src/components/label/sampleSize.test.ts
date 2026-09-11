import { describe, expect, it } from "vitest";

import { convertOnUnitSwitch, normaliseSize, resolveSample } from "./sampleSize";

const TOTAL = 600;

describe("normaliseSize", () => {
  it("replaces rather than concatenates, so a typed value never gains a leading zero", () => {
    // Regression: the old numeric input rendered "0100" when 100 was typed into an empty field.
    expect(normaliseSize("0100", "count", TOTAL)).toBe("100");
    expect(normaliseSize("007", "count", TOTAL)).toBe("7");
  });

  it("keeps the field clearable and strips non-digits", () => {
    expect(normaliseSize("", "count", TOTAL)).toBe("");
    expect(normaliseSize("12abc", "count", TOTAL)).toBe("12");
    expect(normaliseSize("-5", "count", TOTAL)).toBe("5");
  });

  it("clamps to the unit's range", () => {
    expect(normaliseSize("999", "percent", TOTAL)).toBe("100");
    expect(normaliseSize("9999", "count", TOTAL)).toBe("600");
  });
});

describe("resolveSample", () => {
  it("reports both readings against the dataset total", () => {
    expect(resolveSample("100", "count", TOTAL)).toEqual({ valid: true, posts: 100, percent: 17 });
    expect(resolveSample("25", "percent", TOTAL)).toEqual({ valid: true, posts: 150, percent: 25 });
  });

  it("rejects empty, zero and out-of-range values", () => {
    for (const raw of ["", "0", "601"]) {
      expect(resolveSample(raw, "count", TOTAL).valid).toBe(false);
    }
    expect(resolveSample("101", "percent", TOTAL).valid).toBe(false);
  });

  it("never resolves a valid percent to zero posts", () => {
    expect(resolveSample("1", "percent", 10).posts).toBe(1);
  });
});

describe("convertOnUnitSwitch", () => {
  it("preserves the chosen number of posts across a unit change", () => {
    const asCount = resolveSample("150", "count", TOTAL);
    const asPercent = convertOnUnitSwitch(asCount, "percent");
    expect(asPercent).toBe("25");
    expect(resolveSample(asPercent, "percent", TOTAL).posts).toBe(150);
  });
});
