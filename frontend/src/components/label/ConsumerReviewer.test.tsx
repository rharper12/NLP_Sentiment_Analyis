// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ComponentProps } from "react";

import { api } from "../../api/client";
import type { ConsumerCounts, ConsumerPolicy, DatasetSummary, EligibilityPage, LabelSummary } from "../../api/types";
import { ConsumerReviewer } from "./ConsumerReviewer";

vi.mock("../../api/client", async (original) => ({ ...await original<typeof import("../../api/client")>(), api: { eligibilityPage: vi.fn(), reviewEligibility: vi.fn(), manualLabels: vi.fn() } }));

const policy: ConsumerPolicy = { version: "consumer-reactions-v1", start_date: "2026-09-09", end_date: "2026-09-10", timezone: "America/Chicago", per_author_limit: 2, reviewed_target: 500, duplicate_threshold: 0.9, selection_rule: "daily-quotas-recency;author-earliest-id" };
const counts: ConsumerCounts = { retrieved: 3, unique_records: 2, screened_candidates: 1, pending_eligibility: 1, human_inclusions: 0, human_exclusions: 0, included: 0, author_cap_held: 0, missing_author: 0, pending_sentiment: 0, reviewed_final: 0, reviewed_target: 500, shortfall: 500, days: [{ day: "2026-09-09", candidates: 1, included: 0, reviewed_final: 0 }, { day: "2026-09-10", candidates: 0, included: 0, reviewed_final: 0 }] };
const page: EligibilityPage = { total: 1, offset: 0, included_ids: [], counts, items: [{ id: "one", text: "I hate the iPhone Duo", source_type: "x", created_at: "2026-09-09T07:00:00Z", author_id: "0001", comprehend_label: "positive", comprehend_confidence: 0.8, screening: { decision: "include", evidence: ["Personal reaction"], policy_version: "consumer-reactions-v1" } }] };
const dataset: DatasetSummary = { dataset_id: "consumer", source_type: "x", query: "iPhone Duo", record_count: 1, labelled_count: 0, truncated_reason: null, partial: false, preview: [] };
const labels: LabelSummary = { total: 1, labelled: 1, by_source: {}, by_label: {}, review_sample_size: 1, reviewed: 1, manual_vs_comprehend_agreement: null, disagreements: 0, manually_reviewed: 1, machine_scored: 1, comparable_records: 1, agreements: 0 };

beforeEach(() => {
  vi.mocked(api.eligibilityPage).mockResolvedValue(page);
  vi.mocked(api.reviewEligibility).mockResolvedValue(dataset);
  vi.mocked(api.manualLabels).mockResolvedValue(labels);
});
afterEach(() => { cleanup(); vi.resetAllMocks(); });
async function start(extra: Partial<ComponentProps<typeof ConsumerReviewer>> = {}) {
  render(<ConsumerReviewer datasetId="consumer" policy={policy} onBack={vi.fn()} onContinue={vi.fn()} {...extra} />);
  await screen.findByText("I hate the iPhone Duo");
}

it("hides sentiment during eligibility and persists unchanged confirmations", async () => {
  await start();
  expect(screen.queryByText(/Original automated suggestion/)).toBeNull();
  expect(screen.queryByRole("button", { name: /positive/i })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Save eligibility decision" }));
  await waitFor(() => expect(api.reviewEligibility).toHaveBeenCalledWith("consumer", [{ id: "one", decision: "include", reason: null, note: "" }]));
  expect(await screen.findByText("Decision saved.")).toBeTruthy();
  expect(api.manualLabels).not.toHaveBeenCalled();
});

it("requires a reason and explanation for an exclusion override, retaining failed edits", async () => {
  await start();
  fireEvent.change(screen.getByLabelText("Eligibility decision"), { target: { value: "exclude" } });
  const save = screen.getByRole("button", { name: "Save eligibility decision" });
  expect(save.hasAttribute("disabled")).toBe(true);
  fireEvent.change(screen.getByLabelText("Eligibility reason"), { target: { value: "news_or_article" } });
  fireEvent.change(screen.getByLabelText(/Decision note/), { target: { value: "This was a headline quote" } });
  vi.mocked(api.reviewEligibility).mockRejectedValueOnce(new Error("offline"));
  fireEvent.click(save);
  await screen.findByText(/Not saved/);
  expect((screen.getByLabelText(/Decision note/) as HTMLTextAreaElement).value).toBe("This was a headline quote");
  fireEvent.click(screen.getByRole("button", { name: "Save eligibility decision" }));
  await screen.findByText("Decision saved.");
  expect(api.reviewEligibility).toHaveBeenLastCalledWith("consumer", [{ id: "one", decision: "exclude", reason: "news_or_article", note: "This was a headline quote" }]);
});

it("supports scoped sentiment keys and saves an unchanged label", async () => {
  await start();
  vi.mocked(api.eligibilityPage).mockResolvedValue({ ...page, included_ids: ["one"], items: [{ ...page.items[0], eligibility: "include", eligibility_reviewed: true, label: "positive", sentiment_reviewed: false }] });
  fireEvent.change(screen.getByLabelText("Review queue"), { target: { value: "sentiment" } });
  const button = await screen.findByRole("button", { name: "positive (1)" });
  expect(screen.getByText(/Original automated suggestion: positive/)).toBeTruthy();
  fireEvent.keyDown(document.body, { key: "1" });
  expect(api.manualLabels).not.toHaveBeenCalled();
  fireEvent.keyDown(button, { key: "1" });
  await waitFor(() => expect(api.manualLabels).toHaveBeenCalledWith("consumer", [{ id: "one", label: "positive" }]));
});

it("prevents navigation and duplicate saves while a decision is in flight", async () => {
  let resolve: (value: DatasetSummary) => void = () => {};
  vi.mocked(api.reviewEligibility).mockReturnValue(new Promise((done) => { resolve = done; }));
  const request = vi.fn();
  await start({ collection: { ...dataset, consumer_policy: policy, candidate_target: 20, can_collect_more: true }, onAdditional: request });
  fireEvent.click(screen.getByRole("button", { name: "Save eligibility decision" }));
  expect(screen.getByRole("button", { name: "Continue to Export →" }).hasAttribute("disabled")).toBe(true);
  expect(screen.getByRole("button", { name: "Collect additional candidates" }).hasAttribute("disabled")).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Collect additional candidates" }));
  expect(request).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Save eligibility decision" }));
  expect(api.reviewEligibility).toHaveBeenCalledTimes(1);
  await act(async () => resolve(dataset));
  await screen.findByText("Decision saved.");
});
