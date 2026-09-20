// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { StrictMode } from "react";

import { api } from "../../api/client";
import type { LabelSummary, ReviewPage } from "../../api/types";
import { Reviewer } from "./Reviewer";

vi.mock("../../api/client", async (original) => ({
  ...await original<typeof import("../../api/client")>(),
  api: { reviewPage: vi.fn(), manualLabels: vi.fn() },
}));

const saved: LabelSummary = { total: 11, labelled: 11, by_source: {}, by_label: {}, review_sample_size: 11, reviewed: 11, manual_vs_comprehend_agreement: null, disagreements: 0, manually_reviewed: 0, machine_scored: 0, comparable_records: 0, agreements: 0 };
const onDone = vi.fn();
const onError = vi.fn();

function deferred() {
  let resolve: (value: LabelSummary) => void = () => {};
  let reject: (reason: Error) => void = () => {};
  const promise = new Promise<LabelSummary>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve: () => resolve(saved), reject };
}

async function start(count: number) {
  vi.mocked(api.reviewPage).mockResolvedValue({ total: count, offset: 0, items: Array.from({ length: count }, (_, i) => ({ id: `r${i}`, text: `Post ${i}`, source_type: "csv" })) });
  render(<Reviewer datasetId="dataset" onError={onError} onDone={onDone} />);
  await screen.findByText("Post 0");
}

function choose(count: number) {
  for (let i = 0; i < count; i++) fireEvent.click(screen.getByRole("button", { name: /^positive/i }));
}

beforeEach(() => { vi.mocked(api.manualLabels).mockResolvedValue(saved); });
afterEach(() => { cleanup(); vi.resetAllMocks(); });

it("a failed final save prevents completion and keeps decisions retryable", async () => {
  vi.mocked(api.manualLabels).mockRejectedValueOnce(new Error("offline"));
  await start(1);
  choose(1);
  fireEvent.click(screen.getByRole("button", { name: "Finish" }));
  expect(await screen.findByRole("alert")).toHaveProperty("textContent", expect.stringContaining("offline"));
  expect(onDone).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Finish" }));
  await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
  expect(api.manualLabels).toHaveBeenNthCalledWith(2, "dataset", [{ id: "r0", label: "positive" }]);
});

it("Finish waits for an automatic save even when the pending buffer is empty", async () => {
  const first = deferred();
  vi.mocked(api.manualLabels).mockReturnValueOnce(first.promise);
  await start(10);
  choose(10);
  expect(api.manualLabels).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Finish" }));
  expect(onDone).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Saving labels…" }).hasAttribute("disabled")).toBe(true);
  await act(async () => first.resolve());
  await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
  expect(api.manualLabels).toHaveBeenCalledTimes(1);
});

it("multiple failures remain actionable until an explicit retry succeeds", async () => {
  vi.mocked(api.manualLabels).mockRejectedValueOnce(new Error("first failure")).mockRejectedValueOnce(new Error("second failure"));
  await start(1);
  choose(1);
  fireEvent.click(screen.getByRole("button", { name: "Finish" }));
  await screen.findByText(/first failure/);
  fireEvent.click(screen.getByRole("button", { name: "Retry save" }));
  await screen.findByText(/second failure/);
  expect(onDone).not.toHaveBeenCalled();
  expect(onError).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole("button", { name: "Finish" }));
  await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
  expect(api.manualLabels).toHaveBeenCalledTimes(3);
  for (const [, batch] of vi.mocked(api.manualLabels).mock.calls) expect(batch).toEqual([{ id: "r0", label: "positive" }]);
});

it("serializes a correction behind the earlier in-flight decision", async () => {
  const first = deferred(), correction = deferred();
  vi.mocked(api.manualLabels).mockReturnValueOnce(first.promise).mockReturnValueOnce(correction.promise);
  await start(10);
  choose(10);
  for (let i = 0; i < 10; i++) fireEvent.click(screen.getByRole("button", { name: /Previous/ }));
  fireEvent.click(screen.getByRole("button", { name: /^negative/i }));
  fireEvent.click(screen.getByRole("button", { name: "Stop here and keep labels" }));
  expect(api.manualLabels).toHaveBeenCalledTimes(1);
  expect(onDone).not.toHaveBeenCalled();
  await act(async () => first.resolve());
  await waitFor(() => expect(api.manualLabels).toHaveBeenCalledTimes(2));
  expect(api.manualLabels).toHaveBeenNthCalledWith(2, "dataset", [{ id: "r0", label: "negative" }]);
  expect(onDone).not.toHaveBeenCalled();
  await act(async () => correction.resolve());
  await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
});

it("drains both the automatic batch and trailing decisions before completion", async () => {
  const first = deferred(), tail = deferred();
  vi.mocked(api.manualLabels).mockReturnValueOnce(first.promise).mockReturnValueOnce(tail.promise);
  await start(11);
  choose(11);
  fireEvent.click(screen.getByRole("button", { name: "Finish" }));
  expect(api.manualLabels).toHaveBeenCalledTimes(1);
  await act(async () => first.resolve());
  await waitFor(() => expect(api.manualLabels).toHaveBeenCalledTimes(2));
  expect(onDone).not.toHaveBeenCalled();
  expect(api.manualLabels).toHaveBeenNthCalledWith(2, "dataset", [{ id: "r10", label: "positive" }]);
  await act(async () => tail.resolve());
  await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
});

it("a failed in-flight automatic save cannot be bypassed by Finish", async () => {
  const first = deferred();
  vi.mocked(api.manualLabels).mockReturnValueOnce(first.promise);
  await start(10);
  choose(10);
  fireEvent.click(screen.getByRole("button", { name: "Finish" }));
  await act(async () => first.reject(new Error("automatic save failed")));
  await screen.findByText(/automatic save failed/);
  expect(onDone).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Finish" }));
  await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
  expect(vi.mocked(api.manualLabels).mock.calls[1][1]).toHaveLength(10);
});

it("replaces the request aborted by Strict Mode and accepts only its replacement", async () => {
  const pages: { signal: AbortSignal; resolve: (page: import("../../api/types").ReviewPage) => void }[] = [];
  vi.mocked(api.reviewPage).mockImplementation((_id, _offset, _limit, signal) => new Promise((resolve) => pages.push({ signal: signal!, resolve })));
  render(<StrictMode><Reviewer datasetId="dataset" onError={onError} onDone={onDone} /></StrictMode>);
  expect(pages).toHaveLength(2);
  expect(pages[0].signal.aborted).toBe(true);
  expect(pages[1].signal.aborted).toBe(false);
  await act(async () => { pages[0].resolve({ total: 1, offset: 0, items: [{ id: "old", text: "Obsolete post", source_type: "csv" }] }); });
  expect(screen.queryByText("Obsolete post")).toBeNull();
  await act(async () => { pages[1].resolve({ total: 1, offset: 0, items: [{ id: "new", text: "Replacement post", source_type: "csv" }] }); });
  expect(screen.getByText("Replacement post")).toBeTruthy();
  expect(api.reviewPage).toHaveBeenCalledTimes(2);
});

it.each([0, 2])("retries a failed page at offset %i without refetching successful pages", async (offset) => {
  const page = (start: number, count: number): ReviewPage => ({ total: offset + 1, offset: start, items: Array.from({ length: count }, (_, i) => ({ id: `r${start + i}`, text: `Post ${start + i}`, source_type: "csv" })) });
  if (offset) vi.mocked(api.reviewPage).mockResolvedValueOnce(page(0, offset));
  vi.mocked(api.reviewPage).mockRejectedValueOnce(new Error("page offline")).mockResolvedValueOnce(page(offset, 1));
  render(<Reviewer datasetId="dataset" onError={onError} onDone={onDone} />);
  if (offset) { await screen.findByText("Post 0"); choose(offset); }
  await screen.findByText(/page offline/);
  expect(api.reviewPage).toHaveBeenCalledTimes(offset ? 2 : 1);
  fireEvent.click(screen.getByRole("button", { name: "Retry loading posts" }));
  await screen.findByText(`Post ${offset}`);
  choose(1);
  fireEvent.click(screen.getByRole("button", { name: "Finish" }));
  await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
  expect(vi.mocked(api.reviewPage).mock.calls.map((call) => call[1])).toEqual(offset ? [0, offset, offset] : [0, 0]);
  expect(vi.mocked(api.manualLabels).mock.calls.flatMap((call) => call[1])).toHaveLength(offset + 1);
});

it("aborts an outstanding page on unmount and ignores late failure", async () => {
  let reject!: (error: Error) => void;
  vi.mocked(api.reviewPage).mockReturnValue(new Promise((_resolve, no) => { reject = no; }));
  const view = render(<Reviewer datasetId="dataset" onError={onError} onDone={onDone} />);
  const signal = vi.mocked(api.reviewPage).mock.calls[0][3]!;
  view.unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => reject(new Error("late failure")));
  expect(onError).not.toHaveBeenCalled();
  expect(onDone).not.toHaveBeenCalled();
  expect(api.reviewPage).toHaveBeenCalledTimes(1);
});

it("counts final unique decisions, not repeated corrections, for live agreement", async () => {
  vi.mocked(api.reviewPage).mockResolvedValue({ total: 2, offset: 0, items: [{ id: "a", text: "Machine scored", source_type: "csv", comprehend_label: "positive" }, { id: "b", text: "Manual only", source_type: "csv" }] });
  render(<Reviewer datasetId="dataset" onError={onError} onDone={onDone} />);
  await screen.findByText("Machine scored");
  fireEvent.click(screen.getByRole("button", { name: /^positive/i }));
  fireEvent.click(screen.getByRole("button", { name: /Previous/ }));
  fireEvent.click(screen.getByRole("button", { name: /^negative/i }));
  expect(screen.getByText(/agree with Comprehend/).textContent).toContain("0%");
  fireEvent.click(screen.getByRole("button", { name: /^positive/i }));
  expect(screen.getByText(/agree with Comprehend/).textContent).toContain("0%");
});
