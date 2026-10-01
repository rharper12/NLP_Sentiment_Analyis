// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import type { ConsumerCounts, DatasetSummary } from "../../api/types";
import { AdditionalCandidates } from "./AdditionalCandidates";

const counts: ConsumerCounts = { retrieved: 500, unique_records: 500, screened_candidates: 500, pending_eligibility: 0, human_inclusions: 350, human_exclusions: 150, included: 350, author_cap_held: 0, missing_author: 0, pending_sentiment: 0, reviewed_final: 350, reviewed_target: 500, shortfall: 150, days: [] };
const dataset: DatasetSummary = {
  dataset_id: "consumer", source_type: "x", query: "iPhone Duo", record_count: 500, labelled_count: 350, truncated_reason: null, partial: false, preview: [], candidate_target: 500, can_collect_more: true, consumer_counts: counts,
  consumer_policy: { version: "consumer-reactions-v1", start_date: "2026-09-09", end_date: "2026-09-10", timezone: "America/Chicago", per_author_limit: 2, reviewed_target: 500, duplicate_threshold: 0.9, selection_rule: "daily-quotas-recency;author-earliest-id" },
};
afterEach(cleanup);

it("requests the shortfall only on a click and stays available after another partial review", () => {
  const request = vi.fn();
  const { rerender } = render(<AdditionalCandidates dataset={dataset} busy={false} costPerRead={0.005} onRequest={request} />);
  expect(request).not.toHaveBeenCalled();
  expect((screen.getByLabelText("Additional candidate quota") as HTMLInputElement).value).toBe("150");
  expect(screen.getByText(/\$0.75/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Collect additional candidates" }));
  expect(request).toHaveBeenCalledWith(650);
  const remaining = { ...counts, reviewed_final: 450, shortfall: 50 };
  rerender(<AdditionalCandidates dataset={{ ...dataset, candidate_target: 650, record_count: 650 }} counts={remaining} busy={false} onRequest={request} />);
  expect((screen.getByLabelText("Additional candidate quota") as HTMLInputElement).value).toBe("50");
  expect(request).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Collect additional candidates" }));
  expect(request).toHaveBeenLastCalledWith(700);
  rerender(<AdditionalCandidates dataset={dataset} counts={{ ...counts, reviewed_final: 500, shortfall: 0 }} busy={false} onRequest={request} />);
  expect(screen.queryByRole("button")).toBeNull();
  expect(screen.getByText(/Reviewed target reached/)).toBeTruthy();
  rerender(<AdditionalCandidates dataset={dataset} busy={false} onRequest={request} />);
  expect(screen.getByRole("button", { name: "Collect additional candidates" })).toBeTruthy();
});

it("resumes the same authorized target after a budget pause without increasing it", () => {
  const request = vi.fn();
  render(<AdditionalCandidates dataset={{ ...dataset, candidate_target: 650, record_count: 550, partial: true, truncated_reason: "request budget reached" }} busy={false} onRequest={request} />);
  expect(screen.queryByLabelText("Additional candidate quota")).toBeNull();
  expect(screen.getByText(/request budget reached/)).toBeTruthy();
  expect(screen.queryByText(/Provider retry time/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Resume candidate request" }));
  expect(request).toHaveBeenCalledWith(650);
});

it.each([
  [{ can_collect_more: false, truncated_reason: "no more matching posts" }, /Additional collection is unavailable/],
  [{ candidate_target: 5000, record_count: 5000 }, /5,000-candidate target limit/],
])("explains an unavailable collection without a paid action", (changes, message) => {
  const request = vi.fn();
  render(<AdditionalCandidates dataset={{ ...dataset, ...changes }} busy={false} onRequest={request} />);
  expect(screen.getByText(message)).toBeTruthy();
  expect(screen.queryByRole("button")).toBeNull();
  expect(request).not.toHaveBeenCalled();
});

it("permits a smaller batch, rejects invalid sizes, and locks requests while saving", () => {
  const request = vi.fn();
  const { rerender } = render(<AdditionalCandidates dataset={dataset} busy={false} onRequest={request} />);
  const input = screen.getByLabelText("Additional candidate quota");
  const button = screen.getByRole("button", { name: "Collect additional candidates" });
  for (const value of [0, -1, 1.5, 5000]) {
    fireEvent.change(input, { target: { value: String(value) } });
    expect(button.hasAttribute("disabled")).toBe(true);
  }
  fireEvent.change(input, { target: { value: "75" } });
  fireEvent.click(button);
  expect(request).toHaveBeenCalledWith(575);
  rerender(<AdditionalCandidates dataset={dataset} busy onRequest={request} />);
  expect(button.hasAttribute("disabled")).toBe(true);
  expect(input.hasAttribute("disabled")).toBe(true);
});

it("adds quota beyond already-retained page overage and observes the overall cap", () => {
  const request = vi.fn();
  render(<AdditionalCandidates dataset={{ ...dataset, candidate_target: 4980, record_count: 4990 }} busy={false} onRequest={request} />);
  expect((screen.getByLabelText("Additional candidate quota") as HTMLInputElement).value).toBe("10");
  fireEvent.click(screen.getByRole("button", { name: "Collect additional candidates" }));
  expect(request).toHaveBeenCalledWith(5000);
});
