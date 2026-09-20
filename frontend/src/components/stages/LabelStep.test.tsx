// @vitest-environment jsdom

import { act, fireEvent, render, screen, waitFor, cleanup } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { api } from "../../api/client";
import { LabelStep } from "./LabelStep";

vi.mock("../../api/client", async (original) => ({ ...await original<typeof import("../../api/client")>(), api: {
  labelSummary: vi.fn(), chooseReview: vi.fn(), labelEstimate: vi.fn(), labelComprehend: vi.fn(),
} }));
vi.mock("../label/ConfirmDialog", () => ({ ConfirmDialog: ({ onConfirm }: { onConfirm: () => void }) => <button onClick={onConfirm}>Confirm test job</button> }));
vi.mock("../label/ReviewChoice", () => ({ ReviewChoice: ({ onBack, onChoose }: { onBack: () => void; onChoose: (mode: "none", size: number, unit: "count") => void }) => <><p>Choose review</p><button onClick={onBack}>Label more</button><button onClick={() => onChoose("none", 0, "count")}>Skip review</button></> }));

afterEach(() => { cleanup(); vi.clearAllMocks(); });

beforeEach(() => {
  vi.mocked(api.labelSummary).mockResolvedValue({ total: 2, labelled: 0, by_source: {}, by_label: {}, review_sample_size: 0, reviewed: 0, manual_vs_comprehend_agreement: null, disagreements: 0, manually_reviewed: 0, machine_scored: 0, comparable_records: 0, agreements: 0 });
  vi.mocked(api.labelEstimate).mockResolvedValue({ truncated_records: 0, prefix_labels: 0, records_total: 2, records_unlabelled: 2, records_to_send: 2, failed_total: 0, billable_units: 6, unit_chars: 100, min_units_per_document: 3, estimated_cost_usd: null, cost_per_unit_usd: null, price_status: "unavailable", price_fetched_at: null });
});

it("stops after no successful progress and displays partial failures", async () => {
  vi.mocked(api.labelComprehend).mockResolvedValue({ truncated_records: 0, labelled_in_call: 0, labelled_total: 0, remaining: 2, units_billed: 6, cost_usd: null, done: false, partial: true, failed_total: 2, attempted_in_call: 2, failed_in_call: 2 });
  render(<LabelStep datasetId="d" diagnostics={false} comprehendEnabled={null} checkpointLocation={null} onBack={() => {}} onContinue={() => {}} />);
  await waitFor(() => expect(screen.getByText("Label with Comprehend…").hasAttribute("disabled")).toBe(false));
  fireEvent.click(screen.getByText("Label with Comprehend…"));
  fireEvent.click(screen.getByText("Confirm test job"));
  await screen.findByText("Choose review");
  expect(api.labelComprehend).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("status").textContent).toContain("2 posts have no successful Comprehend result");
});

const props = { diagnostics: false, comprehendEnabled: null, checkpointLocation: null, onBack: vi.fn(), onContinue: vi.fn() };
const slice = { truncated_records: 0, labelled_in_call: 1, labelled_total: 1, remaining: 1, units_billed: 3, cost_usd: null, done: false, partial: true, failed_total: 0, attempted_in_call: 1, failed_in_call: 0 };

async function launch() {
  const start = await screen.findByRole("button", { name: "Label with Comprehend…" });
  await waitFor(() => expect(start.hasAttribute("disabled")).toBe(false));
  fireEvent.click(start);
  fireEvent.click(screen.getByText("Confirm test job"));
}

function pendingSlices() {
  const pending: { signal: AbortSignal; resolve: (value: typeof slice) => void }[] = [];
  vi.mocked(api.labelComprehend).mockImplementation((_id, _size, signal) => new Promise((resolve) => pending.push({ signal: signal!, resolve })));
  return pending;
}

it.each(["navigation", "dataset replacement"] as const)("invalidates a paid loop on %s", async (reason) => {
  const pending = pendingSlices();
  const view = render(<LabelStep {...props} datasetId="old" />);
  await launch();
  if (reason === "navigation") view.unmount();
  else view.rerender(<LabelStep {...props} datasetId="new" />);
  expect(pending[0].signal.aborted).toBe(true);
  const summaries = vi.mocked(api.labelSummary).mock.calls.length;
  await act(async () => pending[0].resolve(slice));
  expect(api.labelComprehend).toHaveBeenCalledTimes(1);
  expect(api.labelSummary).toHaveBeenCalledTimes(summaries);
  expect(screen.queryByText("Choose review")).toBeNull();
});

it.each(["cancel", "remount"] as const)("only the new loop can schedule slices after %s and resume", async (reason) => {
  const pending = pendingSlices();
  const view = render(<LabelStep {...props} datasetId="d" />);
  await launch();
  if (reason === "cancel") {
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(await screen.findByText("Label more"));
  } else {
    view.unmount();
    render(<LabelStep {...props} datasetId="d" />);
  }
  await launch();
  expect(pending).toHaveLength(2);
  expect(pending[0].signal.aborted).toBe(true);
  expect(pending[1].signal.aborted).toBe(false);
  await act(async () => pending[0].resolve(slice));
  expect(pending).toHaveLength(2);
  expect(screen.queryByText("Choose review")).toBeNull();
  await act(async () => pending[1].resolve(slice));
  expect(pending).toHaveLength(3);
  await act(async () => pending[2].resolve({ ...slice, remaining: 0, done: true }));
  await screen.findByText("Choose review");
  expect(pending).toHaveLength(3);
});

it.each(["unmount", "replace"])("ignores a late review choice after %s, including its App callback", async (action) => {
  let resolve!: (value: Awaited<ReturnType<typeof api.chooseReview>>) => void;
  vi.mocked(api.chooseReview).mockReturnValue(new Promise((yes) => { resolve = yes; }));
  const onReviewActiveChange = vi.fn();
  const view = render(<LabelStep {...props} datasetId="old" onReviewActiveChange={onReviewActiveChange} />);
  fireEvent.click(screen.getByText("Choose what to label by hand"));
  fireEvent.click(screen.getByText("Skip review"));
  const signal = vi.mocked(api.chooseReview).mock.calls[0][4]!;
  if (action === "unmount") view.unmount();
  else view.rerender(<LabelStep {...props} datasetId="new" onReviewActiveChange={onReviewActiveChange} />);
  expect(signal.aborted).toBe(true);
  await act(async () => resolve(await api.labelSummary("old")));
  expect(onReviewActiveChange).not.toHaveBeenCalled();
  expect(screen.queryByText("Labels ready")).toBeNull();
});
