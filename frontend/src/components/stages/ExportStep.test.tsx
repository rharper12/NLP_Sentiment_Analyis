// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api, ApiError } from "../../api/client";
import type { CheckpointInfo, DatasetSummary } from "../../api/types";
import { ExportStep } from "./ExportStep";

vi.mock("../../api/client", async (original) => ({ ...await original<typeof import("../../api/client")>(), api: { checkpoints: vi.fn(), convertCheckpoint: vi.fn() } }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
const csv: CheckpointInfo = { stage: "collected", format: "csv", uri: "/snapshot", bytes: 10, written_at: "2026-09-20T00:00:00Z", status: "current", revision: "one" };
const dataset: DatasetSummary = { dataset_id: "d", source_type: "csv", query: null, record_count: 500, labelled_count: 0, truncated_reason: null, partial: false, preview: [] };

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
