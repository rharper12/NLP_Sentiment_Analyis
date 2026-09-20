// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { labelEstimateSchema, labelProgressSchema } from "../../api/validation";
import { ConfirmDialog } from "./ConfirmDialog";
import { CostChip } from "./CostChip";

afterEach(cleanup);

const unavailable = {
  records_total: 2, records_unlabelled: 2, failed_total: 0, records_to_send: 2, billable_units: 28,
  unit_chars: 100, min_units_per_document: 3, truncated_records: 1, prefix_labels: 0,
  estimated_cost_usd: null, cost_per_unit_usd: null, price_status: "unavailable",
  price_fetched_at: null,
};

it("keeps unavailable pricing nullable through validation and presentation", () => {
  const estimate = labelEstimateSchema.parse(unavailable);
  expect(estimate.estimated_cost_usd).toBeNull();
  expect(labelProgressSchema.parse({ labelled_in_call: 2, labelled_total: 2, remaining: 0,
    units_billed: 28, cost_usd: null, done: true, partial: false, attempted_in_call: 2, failed_in_call: 0, failed_total: 0, truncated_records: 1 }).cost_usd).toBeNull();
  render(<CostChip est={estimate} />);
  expect(screen.getByText("Estimate unavailable right now.")).toBeTruthy();
  expect(screen.queryByText(/\$0/)).toBeNull();
});

it("discloses prefix labels before confirming a paid request", () => {
  HTMLDialogElement.prototype.showModal = vi.fn();
  render(<ConfirmDialog est={labelEstimateSchema.parse(unavailable)} onCancel={() => {}} onConfirm={() => {}} />);
  expect(screen.getByText(/1 oversized documents/).textContent).toContain("labels describe the submitted prefixes");
  expect(screen.getByText(/No current price could be fetched/)).toBeTruthy();
});
