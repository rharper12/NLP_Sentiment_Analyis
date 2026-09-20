// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { LabelSummary } from "../../api/types";
import { SummaryBlock } from "./SummaryBlock";

afterEach(cleanup);

const summary: LabelSummary = {
  total: 10, labelled: 8, by_source: { manual: 5, comprehend: 3 }, by_label: { positive: 8 },
  review_sample_size: 1, reviewed: 1, manually_reviewed: 5, machine_scored: 7,
  comparable_records: 4, agreements: 3, disagreements: 1,
  manual_vs_comprehend_agreement: 0.75, warnings: [],
};

describe("agreement cohort", () => {
  it("uses comparable posts across all manual labels, independent of the selected sample", () => {
    render(<SummaryBlock s={summary} />);
    expect(screen.getByText("75.0%")).toBeTruthy();
    expect(screen.getByText(/of 4 comparable posts across all manual labels/)).toBeTruthy();
  });

  it("does not display a percentage for an empty comparable cohort", () => {
    render(<SummaryBlock s={{ ...summary, comparable_records: 0, agreements: 0, disagreements: 0, manual_vs_comprehend_agreement: null }} />);
    expect(screen.queryByText(/Reviewers agreed/)).toBeNull();
  });
});
