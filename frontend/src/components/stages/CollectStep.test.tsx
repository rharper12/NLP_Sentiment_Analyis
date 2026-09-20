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
