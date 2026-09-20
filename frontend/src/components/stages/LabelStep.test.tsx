// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor, cleanup } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { api } from "../../api/client";
import { LabelStep } from "./LabelStep";

vi.mock("../../api/client", async (original) => ({ ...await original<typeof import("../../api/client")>(), api: {
  labelSummary: vi.fn(), labelEstimate: vi.fn(), labelComprehend: vi.fn(),
} }));
vi.mock("../label/ConfirmDialog", () => ({ ConfirmDialog: ({ onConfirm }: { onConfirm: () => void }) => <button onClick={onConfirm}>Confirm test job</button> }));
vi.mock("../label/ReviewChoice", () => ({ ReviewChoice: () => <p>Choose review</p> }));

afterEach(() => { cleanup(); vi.clearAllMocks(); });

it("stops after no successful progress and displays partial failures", async () => {
  vi.mocked(api.labelSummary).mockResolvedValue({ total: 2, labelled: 0, by_source: {}, by_label: {}, review_sample_size: 0, reviewed: 0, manual_vs_comprehend_agreement: null, disagreements: 0 });
  vi.mocked(api.labelEstimate).mockResolvedValue({ truncated_records: 0, prefix_labels: 0, records_total: 2, records_unlabelled: 2, records_to_send: 2, failed_total: 0, billable_units: 6, unit_chars: 100, min_units_per_document: 3, estimated_cost_usd: null, cost_per_unit_usd: null, price_status: "unavailable", price_fetched_at: null });
  vi.mocked(api.labelComprehend).mockResolvedValue({ truncated_records: 0, labelled_in_call: 0, labelled_total: 0, remaining: 2, units_billed: 6, cost_usd: null, done: false, partial: true, failed_total: 2, attempted_in_call: 2, failed_in_call: 2 });
  render(<LabelStep datasetId="d" diagnostics={false} comprehendEnabled={null} checkpointLocation={null} onBack={() => {}} onContinue={() => {}} />);
  await waitFor(() => expect(screen.getByText("Label with Comprehend…").hasAttribute("disabled")).toBe(false));
  fireEvent.click(screen.getByText("Label with Comprehend…"));
  fireEvent.click(screen.getByText("Confirm test job"));
  await screen.findByText("Choose review");
  expect(api.labelComprehend).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("status").textContent).toContain("2 posts have no successful Comprehend result");
});
