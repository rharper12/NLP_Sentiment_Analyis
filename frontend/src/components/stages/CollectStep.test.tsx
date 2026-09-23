// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { CollectStep } from "./CollectStep";

afterEach(() => { cleanup(); vi.useRealTimers(); });

it("submits September 9 through today as custom historical dates", () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-21T12:00:00Z"));
  const search = vi.fn();
  render(<CollectStep busy={false} error={null} xConfigured dataset={null} onSearch={search} onLoadSample={vi.fn()} onUpload={vi.fn()} onRestore={vi.fn()} onCancel={vi.fn()} onContinue={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("Topic"), { target: { value: '"iPhone Duo"' } });
  fireEvent.click(screen.getByRole("button", { name: "Custom" }));
  const from = screen.getByLabelText("From") as HTMLInputElement;
  const to = screen.getByLabelText("To") as HTMLInputElement;
  expect(from.min).toBe("2006-03-01");
  fireEvent.change(from, { target: { value: "2026-09-09" } });
  fireEvent.change(to, { target: { value: "2026-09-21" } });
  expect(from.checkValidity()).toBe(true);
  expect(to.checkValidity()).toBe(true);
  const submit = screen.getByRole("button", { name: "Search and collect" }) as HTMLButtonElement;
  expect(submit.disabled).toBe(false);
  fireEvent.click(submit);
  expect(search).toHaveBeenCalledWith('"iPhone Duo"', 600, {
    start: "2026-09-09T00:00:00Z", end: "2026-09-21T23:59:59Z",
  });
});

it("offers an explicit resume action for saved partial collection", () => {
  const resume = vi.fn();
  render(<CollectStep busy={false} error={null} xConfigured dataset={{ dataset_id: "x-stable", source_type: "x", query: "topic", record_count: 100, labelled_count: 0, truncated_reason: "request budget reached", partial: true, preview: [] }} onSearch={vi.fn()} onLoadSample={vi.fn()} onUpload={vi.fn()} onRestore={vi.fn()} onCancel={vi.fn()} onContinue={vi.fn()} onResume={resume} />);
  fireEvent.click(screen.getByText("Resume collection"));
  expect(resume).toHaveBeenCalledOnce();
  expect(screen.getByText(/Stopped early/).textContent).toContain("request budget reached");
});

it("uses the server rate for preflight and committed reads for the result", () => {
  render(<CollectStep busy={false} error={null} xConfigured costPerRead={0.017} dataset={{ dataset_id: "x", source_type: "x", query: "test", record_count: 20, billed_reads: 100, committed_cost_usd: 1.7, labelled_count: 0, truncated_reason: null, partial: false, preview: [] }} onSearch={vi.fn()} onLoadSample={vi.fn()} onUpload={vi.fn()} onRestore={vi.fn()} onCancel={vi.fn()} onContinue={vi.fn()} />);
  expect(screen.getByText("$10.20")).toBeTruthy();
  expect(screen.getByText(/posts collected/).textContent).toContain("20");
  expect(screen.getByText(/100 billed reads/).textContent).toContain("committed spend $1.70");
});


it("hides the local picker outside local file-backed development", () => {
  render(<CollectStep busy={false} error={null} xConfigured dataset={null} onSearch={vi.fn()} onLoadSample={vi.fn()} onUpload={vi.fn()} onRestore={vi.fn()} onCancel={vi.fn()} onContinue={vi.fn()} />);
  expect(screen.queryByRole("tab", { name: "Saved datasets" })).toBeNull();
});

it("supports arrow-key navigation between source tabs", () => {
  render(<CollectStep busy={false} error={null} xConfigured dataset={null} onSearch={vi.fn()} onLoadSample={vi.fn()} onUpload={vi.fn()} onRestore={vi.fn()} onCancel={vi.fn()} onContinue={vi.fn()} />);
  fireEvent.keyDown(screen.getByRole("tab", { name: "Search X" }), { key: "End" });
  expect(screen.getByRole("tab", { name: "Upload CSV" }).getAttribute("aria-selected")).toBe("true");
  expect(screen.getByRole("button", { name: "Import CSV" }).hasAttribute("disabled")).toBe(true);
  expect(document.activeElement).toBe(screen.getByRole("tab", { name: "Upload CSV" }));
});
