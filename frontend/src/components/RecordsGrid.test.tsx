// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import type { RecordPair } from "../api/types";
import { RecordsGrid } from "./RecordsGrid";

function readBlob(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(blob);
  });
}

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("exports spreadsheet-safe grid text while keeping source IDs and row values unchanged", async () => {
  const create = vi.fn<(blob: Blob) => string>(() => "blob:grid-csv");
  vi.stubGlobal("URL", class extends URL {
    static createObjectURL = create;
    static revokeObjectURL = vi.fn();
  });
  // jsdom rejects Vitest's Window proxy in MouseEvent.view; keep the native event otherwise.
  vi.stubGlobal("MouseEvent", class extends MouseEvent {
    constructor(type: string, init: MouseEventInit = {}) { super(type, { ...init, view: null }); }
  });
  vi.spyOn(HTMLAnchorElement.prototype, "dispatchEvent").mockReturnValue(true);
  const pairs: RecordPair[] = ["00123", "99999999999999999999999999"].map((id) => ({
    original: { id, text: "\t=1+1", label: "+SUM(A1)", label_confidence: 0.85, source_type: "csv" },
    processed: { id, text: " @SUM(A1)", source_type: "csv" },
  }));
  const select = vi.fn();
  const { container } = render(<RecordsGrid pairs={pairs} theme="light" hasRun onSelect={select} />);
  await waitFor(() => expect(container.querySelector('[row-id="00123"]')).not.toBeNull());
  expect(container.querySelector('[row-id="99999999999999999999999999"]')).not.toBeNull();
  fireEvent.click(container.querySelector('[row-id="00123"] [col-id="original"]')!);
  await waitFor(() => expect(select).toHaveBeenCalledWith(pairs[0]));
  fireEvent.click(screen.getByRole("button", { name: "Export view" }));
  await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
  const csv = await readBlob(create.mock.calls[0][0]);
  expect(csv).toContain("'\t=1+1");
  expect(csv).toContain("'+SUM(A1)");
  expect(csv).toContain("85%");
  expect(csv).toContain("' @SUM(A1)");
  expect(pairs[0].original.text).toBe("\t=1+1");
});
