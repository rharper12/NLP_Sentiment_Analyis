// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import type { CsvValidation } from "../../api/types";
import { CollectStep } from "../stages/CollectStep";

vi.mock("../../api/client", async (original) => ({ ...await original<typeof import("../../api/client")>(), api: { validateCsv: vi.fn() } }));
const valid: CsvValidation = { record_count: 500, skipped_empty: 1, labelled_count: 0, preview: [{ id: "1", text: "Great phone", source_type: "csv" }] };
const file = (name = "posts.csv") => new File(["text\nGreat phone"], name, { type: "text/csv" });
const upload = vi.fn();

function start() {
  render(<CollectStep busy={false} error={null} xConfigured dataset={null} onUpload={upload} onSearch={vi.fn()} onLoadSample={vi.fn()} onRestore={vi.fn()} onCancel={vi.fn()} onContinue={vi.fn()} />);
  fireEvent.click(screen.getByRole("tab", { name: "Upload CSV" }));
}
beforeEach(() => { vi.mocked(api.validateCsv).mockResolvedValue(valid); });
afterEach(() => { cleanup(); vi.resetAllMocks(); });

it("validates a chosen file and previews rows before enabling import", async () => {
  start();
  const selected = file();
  const submit = screen.getByRole("button", { name: "Import CSV" });
  expect(submit.hasAttribute("disabled")).toBe(true);
  fireEvent.change(screen.getByLabelText("CSV file"), { target: { files: [selected] } });
  expect(submit.hasAttribute("disabled")).toBe(true);
  await screen.findByText("Great phone");
  expect(screen.getByRole("status").textContent).toContain("1 blank text rows");
  expect(upload).not.toHaveBeenCalled();
  fireEvent.click(submit);
  expect(upload).toHaveBeenCalledWith(selected);
});

it("supports dropping a file using the same validation path", async () => {
  start();
  const selected = file();
  fireEvent.drop(screen.getByText("Drag and drop a CSV file here").parentElement!, { dataTransfer: { files: [selected] } });
  await waitFor(() => expect(api.validateCsv).toHaveBeenCalledWith(selected, expect.any(AbortSignal)));
  await screen.findByText("Great phone");
});

it.each(["wrong extension", "empty", "oversize", "multiple"])("rejects %s before sending data", async (reason) => {
  start();
  const bad = reason === "empty" ? new File([], "empty.csv") : reason === "wrong extension" ? file("data.json") : file();
  if (reason === "oversize") Object.defineProperty(bad, "size", { value: 4 * 1024 * 1024 + 1 });
  fireEvent.drop(screen.getByText("Drag and drop a CSV file here").parentElement!, { dataTransfer: { files: reason === "multiple" ? [bad, file()] : [bad] } });
  await screen.findByRole("alert");
  expect(api.validateCsv).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Import CSV" }).hasAttribute("disabled")).toBe(true);
});

it("invalidates an earlier validation when a replacement fails", async () => {
  let resolve!: (value: CsvValidation) => void;
  vi.mocked(api.validateCsv).mockReturnValueOnce(new Promise((yes) => { resolve = yes; })).mockRejectedValueOnce(new Error("CSV needs a 'text' column"));
  start();
  fireEvent.change(screen.getByLabelText("CSV file"), { target: { files: [file("first.csv")] } });
  const signal = vi.mocked(api.validateCsv).mock.calls[0][1]!;
  fireEvent.change(screen.getByLabelText("CSV file"), { target: { files: [file("second.csv")] } });
  await screen.findByRole("alert");
  expect(signal.aborted).toBe(true);
  await act(async () => resolve(valid));
  expect(screen.queryByText("Great phone")).toBeNull();
  expect(screen.getByRole("button", { name: "Import CSV" }).hasAttribute("disabled")).toBe(true);
});

it("cancels validation when leaving the upload tab", async () => {
  let resolve!: (value: CsvValidation) => void;
  vi.mocked(api.validateCsv).mockReturnValueOnce(new Promise((yes) => { resolve = yes; }));
  start();
  fireEvent.change(screen.getByLabelText("CSV file"), { target: { files: [file()] } });
  const signal = vi.mocked(api.validateCsv).mock.calls[0][1]!;
  fireEvent.click(screen.getByRole("tab", { name: "Search X" }));
  await act(async () => resolve(valid));
  expect(signal.aborted).toBe(true);
  fireEvent.click(screen.getByRole("tab", { name: "Upload CSV" }));
  expect(screen.getByRole("button", { name: "Import CSV" }).hasAttribute("disabled")).toBe(true);
});
