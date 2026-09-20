import { expect, it } from "vitest";

import { spreadsheetText } from "./spreadsheet";

it.each(["=1+1", "+SUM(A1)", "-1+2", "@SUM(A1)"])("neutralizes %s after whitespace and controls", (formula) => {
  for (const leading of ["", " ", "\t", "\r", "\n", "\u00a0", "\u200b", "\ufeff", "\x00", "\x01", "\x7f"]) {
    expect(spreadsheetText(leading + formula)).toBe(`'${leading}${formula}`);
  }
});

it("preserves ordinary strings", () => {
  for (const text of ["", "ordinary text", "00123", "not = a formula", "'already text"]) expect(spreadsheetText(text)).toBe(text);
});
