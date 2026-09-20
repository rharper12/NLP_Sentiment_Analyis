// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { CollectStep } from "./CollectStep";

afterEach(cleanup);

it("offers an explicit resume action for saved partial collection", () => {
  const resume = vi.fn();
  render(<CollectStep busy={false} error={null} xConfigured dataset={{ dataset_id: "x-stable", source_type: "x", query: "topic", record_count: 100, labelled_count: 0, truncated_reason: "request budget reached", partial: true, preview: [] }} onSearch={vi.fn()} onLoadSample={vi.fn()} onUpload={vi.fn()} onCancel={vi.fn()} onContinue={vi.fn()} onResume={resume} />);
  fireEvent.click(screen.getByText("Resume collection"));
  expect(resume).toHaveBeenCalledOnce();
  expect(screen.getByText(/Stopped early/).textContent).toContain("request budget reached");
});

it("uses the server rate for preflight and committed reads for the result", () => {
  render(<CollectStep busy={false} error={null} xConfigured costPerRead={0.017} dataset={{ dataset_id: "x", source_type: "x", query: "test", record_count: 20, billed_reads: 100, committed_cost_usd: 1.7, labelled_count: 0, truncated_reason: null, partial: false, preview: [] }} onSearch={vi.fn()} onLoadSample={vi.fn()} onUpload={vi.fn()} onCancel={vi.fn()} onContinue={vi.fn()} />);
  expect(screen.getByText("$10.20")).toBeTruthy();
  expect(screen.getByText(/posts collected/).textContent).toContain("20");
  expect(screen.getByText(/100 billed reads/).textContent).toContain("committed spend $1.70");
});
