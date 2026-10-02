// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import type { ConsumerCounts, DatasetSummary } from "../../api/types";
import { AdditionalCandidates } from "./AdditionalCandidates";

const counts: ConsumerCounts = { retrieved: 500, unique_records: 500, screened_candidates: 500, pending_eligibility: 0, human_inclusions: 350, human_exclusions: 150, included: 350, author_cap_held: 0, missing_author: 0, pending_sentiment: 0, reviewed_final: 350, reviewed_target: 500, shortfall: 150, days: [] };
const dataset: DatasetSummary = {
  dataset_id: "consumer", source_type: "x", query: "iPhone Duo", record_count: 500, labelled_count: 350, truncated_reason: null, partial: false, preview: [], candidate_target: 500, can_collect_more: true, consumer_counts: counts,
  consumer_policy: { version: "consumer-reactions-v1", start_date: "2026-09-09", end_date: "2026-09-10", timezone: "America/Chicago", per_author_limit: 2, reviewed_target: 500, duplicate_threshold: 0.9, selection_rule: "daily-quotas-recency;author-earliest-id" },
};
afterEach(() => { cleanup(); vi.useRealTimers(); });

it("requests the shortfall only on a click and stays available after another partial review", () => {
  const request = vi.fn();
  const { rerender } = render(<AdditionalCandidates dataset={dataset} busy={false} costPerRead={0.005} onRequest={request} />);
  expect(request).not.toHaveBeenCalled();
  expect((screen.getByLabelText("Posts to request") as HTMLInputElement).value).toBe("150");
  expect(screen.getByText(/\$0.75/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Get more posts" }));
  expect(request).toHaveBeenCalledWith(650);
  const remaining = { ...counts, reviewed_final: 450, shortfall: 50 };
  rerender(<AdditionalCandidates dataset={{ ...dataset, candidate_target: 650, record_count: 650 }} counts={remaining} busy={false} onRequest={request} />);
  expect((screen.getByLabelText("Posts to request") as HTMLInputElement).value).toBe("50");
  expect(request).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Get more posts" }));
  expect(request).toHaveBeenLastCalledWith(700);
  rerender(<AdditionalCandidates dataset={dataset} counts={{ ...counts, reviewed_final: 500, shortfall: 0 }} busy={false} onRequest={request} />);
  expect(screen.queryByRole("button")).toBeNull();
  expect(screen.getByText(/Reviewed target reached/)).toBeTruthy();
  rerender(<AdditionalCandidates dataset={dataset} busy={false} onRequest={request} />);
  expect(screen.getByRole("button", { name: "Get more posts" })).toBeTruthy();
});

it("resumes the same authorized target after a budget pause without increasing it", () => {
  const request = vi.fn();
  render(<AdditionalCandidates dataset={{ ...dataset, candidate_target: 650, record_count: 550, partial: true, truncated_reason: "request budget reached" }} busy={false} onRequest={request} />);
  expect(screen.queryByLabelText("Posts to request")).toBeNull();
  expect(screen.getByText(/Adds to this dataset/)).toBeTruthy();
  expect(screen.queryByText(/Provider retry time/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Get more posts" }));
  expect(request).toHaveBeenCalledWith(650);
});

it("waits for X's deadline without paying automatically, then resumes the same target", () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-02T01:00:00Z"));
  const request = vi.fn();
  render(<AdditionalCandidates dataset={{ ...dataset, record_count: 439, candidate_target: 520, partial: true, retry_at: Date.now() / 1000 + 65 }} busy={false} onRequest={request} />);
  const paused = screen.getByRole("button", { name: "Get more posts" });
  expect(screen.getByText("1:05")).toBeTruthy();
  expect(paused.hasAttribute("disabled")).toBe(true);
  fireEvent.click(paused);
  expect(request).not.toHaveBeenCalled();
  act(() => vi.advanceTimersByTime(65000));
  const resume = screen.getByRole("button", { name: "Get more posts" });
  expect(resume.hasAttribute("disabled")).toBe(false);
  expect(request).not.toHaveBeenCalled();
  fireEvent.click(resume);
  expect(request).toHaveBeenCalledExactlyOnceWith(520);
});

it.each([
  [{ can_collect_more: false, truncated_reason: "no more matching posts" }, /No more posts are available/],
  [{ candidate_target: 5000, record_count: 5000 }, /5,000-post collection limit/],
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
  const input = screen.getByLabelText("Posts to request");
  const button = screen.getByRole("button", { name: "Get more posts" });
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
  expect((screen.getByLabelText("Posts to request") as HTMLInputElement).value).toBe("10");
  fireEvent.click(screen.getByRole("button", { name: "Get more posts" }));
  expect(request).toHaveBeenCalledWith(5000);
});

it("uses existing unreviewed posts before offering another paid batch", () => {
  const request = vi.fn();
  const available = { ...counts, reviewed_final: 0, shortfall: 500, pending_eligibility: 530 };
  const view = render(<AdditionalCandidates dataset={{ ...dataset, record_count: 530, candidate_target: 530 }} counts={available} busy={false} onRequest={request} />);
  expect(screen.getByText(/530 saved posts still need review/)).toBeTruthy();
  expect(screen.queryByRole("button")).toBeNull();
  view.rerender(<AdditionalCandidates dataset={{ ...dataset, record_count: 530, candidate_target: 530 }} counts={{ ...available, pending_eligibility: 480 }} busy={false} onRequest={request} />);
  expect((screen.getByLabelText("Posts to request") as HTMLInputElement).value).toBe("20");
  expect(request).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Get more posts" }));
  expect(request).toHaveBeenCalledExactlyOnceWith(550);
});
