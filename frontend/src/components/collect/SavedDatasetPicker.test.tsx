// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import type { LocalDatasetPage } from "../../api/types";
import { CollectStep } from "../stages/CollectStep";

vi.mock("../../api/client", async (original) => ({ ...await original<typeof import("../../api/client")>(), api: { localDatasets: vi.fn() } }));
const restore = vi.fn();
const files: LocalDatasetPage = { total: 2, items: [
  { dataset_id: "new", filename: "new.json", modified_at: "2026-09-23T12:00:00Z", bytes: 200 },
  { dataset_id: "old", filename: "old.json", modified_at: "2026-09-22T12:00:00Z", bytes: 100 },
] };
function start() {
  render(<CollectStep busy={false} error={null} xConfigured localDatasetsAvailable dataset={null} onSearch={vi.fn()} onLoadSample={vi.fn()} onUpload={vi.fn()} onRestore={restore} onCancel={vi.fn()} onContinue={vi.fn()} />);
  fireEvent.click(screen.getByRole("tab", { name: "Saved datasets" }));
}
afterEach(() => { cleanup(); vi.resetAllMocks(); });

it("opens the chosen local ID with an explicit selection and preserves newest-first order", async () => {
  vi.mocked(api.localDatasets).mockResolvedValue(files);
  start();
  expect(screen.getByRole("button", { name: "Open in Clean →" }).hasAttribute("disabled")).toBe(true);
  await screen.findByRole("option", { name: /new.json/ });
  expect(screen.getAllByRole("option").slice(1).map((option) => option.getAttribute("value"))).toEqual(["new", "old"]);
  expect(screen.queryByLabelText("Saved original dataset")).toBeNull();
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "old" } });
  fireEvent.click(screen.getByRole("button", { name: "Open in Clean →" }));
  expect(restore).toHaveBeenCalledWith("old");
  fireEvent.click(screen.getByRole("button", { name: "Refresh saved datasets" }));
  await waitFor(() => expect(api.localDatasets).toHaveBeenCalledTimes(2));
  expect(screen.getByRole("button", { name: "Open in Clean →" }).hasAttribute("disabled")).toBe(true);
});

it("shows an actionable empty state", async () => {
  vi.mocked(api.localDatasets).mockResolvedValue({ total: 0, items: [] });
  start();
  await screen.findByText(/No saved datasets yet/);
  expect(screen.getByRole("combobox").hasAttribute("disabled")).toBe(true);
});

it("retries a failed page at the same offset", async () => {
  vi.mocked(api.localDatasets).mockResolvedValueOnce({ total: 51, items: files.items.slice(0, 1) }).mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce({ total: 2, items: files.items.slice(1) });
  start();
  fireEvent.click(await screen.findByRole("button", { name: "Load older datasets" }));
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "Retry loading saved datasets" }));
  await screen.findByRole("option", { name: /old.json/ });
  expect(vi.mocked(api.localDatasets).mock.calls.map(([offset]) => offset)).toEqual([0, 50, 50]);
});

it("ignores late list responses after leaving the picker", async () => {
  let resolve!: (value: LocalDatasetPage) => void;
  vi.mocked(api.localDatasets).mockReturnValueOnce(new Promise((yes) => { resolve = yes; }));
  start();
  const signal = vi.mocked(api.localDatasets).mock.calls[0][1]!;
  fireEvent.click(screen.getByRole("tab", { name: "Search X" }));
  await act(async () => resolve(files));
  expect(signal.aborted).toBe(true);
  expect(screen.queryByRole("combobox")).toBeNull();
});
