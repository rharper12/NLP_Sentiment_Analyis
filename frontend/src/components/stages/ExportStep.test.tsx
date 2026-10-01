// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api, ApiError } from "../../api/client";
import type { CheckpointInfo, DatasetSummary } from "../../api/types";
import { ExportStep } from "./ExportStep";

vi.mock("../../api/client", async (original) => ({ ...await original<typeof import("../../api/client")>(), api: { dataset: vi.fn(), checkpoints: vi.fn(), convertCheckpoint: vi.fn(), download: vi.fn(), save: vi.fn() } }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
const csv: CheckpointInfo = { stage: "collected", format: "csv", uri: "/snapshot", bytes: 10, written_at: "2026-09-20T00:00:00Z", status: "current", revision: "one" };
const dataset: DatasetSummary = { dataset_id: "d", source_type: "csv", query: null, record_count: 500, labelled_count: 0, truncated_reason: null, partial: false, preview: [] };

it("refreshes consumer counts and distinguishes reviewed training rows from candidate archives", async () => {
  const consumer: DatasetSummary = { ...dataset, consumer_policy: { version: "consumer-reactions-v1", start_date: "2026-09-09", end_date: "2026-09-10", timezone: "America/Chicago", per_author_limit: 2, reviewed_target: 500, duplicate_threshold: 0.9, selection_rule: "daily-quotas-recency;author-earliest-id" } };
  vi.mocked(api.dataset).mockResolvedValue({ ...consumer, consumer_counts: { retrieved: 70, unique_records: 65, screened_candidates: 60, pending_eligibility: 10, human_inclusions: 40, human_exclusions: 10, included: 38, author_cap_held: 2, missing_author: 1, pending_sentiment: 8, reviewed_final: 30, reviewed_target: 500, shortfall: 470, days: [] } });
  render(<ExportStep dataset={consumer} run={null} diagnostics={false} onBack={vi.fn()} onStartOver={vi.fn()} />);
  await screen.findByText(/30 of 500 final reviewed examples/);
  expect(screen.getByText(/Partial export: the reviewed target/)).toBeTruthy();
  expect(screen.queryByText(/Use this for Task 2/)).toBeNull();
  expect((screen.getByRole("textbox", { name: "Reviewed consumer Parquet filename" }) as HTMLInputElement).value).toBe("d-reviewed");
  fireEvent.click(screen.getByRole("button", { name: "Download Reviewed consumer Parquet" }));
  await waitFor(() => expect(api.download).toHaveBeenCalledWith("d", "reviewed.parquet", expect.any(AbortSignal), undefined));
});

it.each([new ApiError("Conversion unavailable", 503, "request-test"), new TypeError("Network offline")])("shows conversion failure, retains prior list, then retries successfully: %s", async (failure) => {
  vi.mocked(api.checkpoints).mockResolvedValue({ location: "local", items: [csv] });
  let reject!: (error: Error) => void;
  vi.mocked(api.convertCheckpoint).mockReturnValueOnce(new Promise((_yes, no) => { reject = no; })).mockResolvedValueOnce({ ...csv, format: "parquet" });
  render(<ExportStep dataset={dataset} run={null} diagnostics onBack={vi.fn()} onStartOver={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Convert to Parquet" }));
  expect(screen.getByRole("button", { name: "Converting…" }).hasAttribute("disabled")).toBe(true);
  await act(async () => reject(failure));
  expect((await screen.findByRole("alert")).textContent).toContain(failure.message);
  if (failure instanceof ApiError) expect(screen.getByRole("alert").textContent).toContain("request-test");
  const retry = screen.getByRole("button", { name: "Convert to Parquet" });
  expect(retry.hasAttribute("disabled")).toBe(false);
  expect(api.checkpoints).toHaveBeenCalledTimes(1);
  vi.mocked(api.checkpoints).mockResolvedValue({ location: "local", items: [csv, { ...csv, format: "parquet" }] });
  fireEvent.click(retry);
  await waitFor(() => expect(api.checkpoints).toHaveBeenCalledTimes(2));
  await screen.findByRole("button", { name: "Re-convert to Parquet" });
  expect(screen.queryByRole("alert")).toBeNull();
});

it("marks old converted snapshots as stale", async () => {
  vi.mocked(api.checkpoints).mockResolvedValue({ location: "local", items: [csv, { ...csv, format: "parquet", status: "stale" }] });
  render(<ExportStep dataset={dataset} run={null} diagnostics onBack={vi.fn()} onStartOver={vi.fn()} />);
  expect((await screen.findByText(/parquet · stale/)).textContent).toContain("stale");
});

it("keeps extensions fixed and custom names separate for each export", async () => {
  const file_stem = "iphone-duo-2026-09-23_12-15-30-UTC-0500";
  vi.mocked(api.download).mockResolvedValue(undefined);
  vi.mocked(api.save).mockResolvedValue({ uri: "s3://test/results/" });
  render(<ExportStep dataset={{ ...dataset, file_stem }} run={null} diagnostics={false} onBack={vi.fn()} onStartOver={vi.fn()} />);
  const csvName = screen.getByRole("textbox", { name: "CSV filename" }) as HTMLInputElement;
  expect(csvName.readOnly).toBe(true);
  expect(csvName.value).toBe(file_stem);
  fireEvent.click(screen.getByRole("checkbox", { name: "Custom filename for CSV" }));
  expect(csvName.readOnly).toBe(false);
  fireEvent.change(csvName, { target: { value: "Final results.csv" } });
  expect(screen.getByRole("button", { name: "Download CSV" }).hasAttribute("disabled")).toBe(true);
  expect(csvName.getAttribute("aria-invalid")).toBe("true");
  expect(screen.getByRole("alert").id).toBeTruthy();
  fireEvent.change(csvName, { target: { value: "Final results" } });
  fireEvent.click(screen.getByRole("button", { name: "Download CSV" }));
  await waitFor(() => expect(api.download).toHaveBeenCalledWith("d", "csv", expect.any(AbortSignal), "Final results"));
  expect((screen.getByRole("textbox", { name: "Parquet filename" }) as HTMLInputElement).value).toBe(file_stem);
  fireEvent.click(screen.getByRole("checkbox", { name: "Custom filename for Save to S3" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Save to S3 filename" }), { target: { value: "S3 results" } });
  fireEvent.click(screen.getByRole("button", { name: "Save to S3" }));
  await waitFor(() => expect(api.save).toHaveBeenCalledWith("d", expect.any(AbortSignal), "S3 results"));
  fireEvent.click(screen.getByRole("checkbox", { name: "Custom filename for CSV" }));
  expect(csvName.value).toBe(file_stem);
  fireEvent.click(screen.getByRole("button", { name: "Download CSV" }));
  await waitFor(() => expect(api.download).toHaveBeenLastCalledWith("d", "csv", expect.any(AbortSignal), undefined));
});
