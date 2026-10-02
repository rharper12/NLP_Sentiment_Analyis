// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ComponentProps } from "react";

import { api } from "../../api/client";
import type {
  ConsumerCounts,
  ConsumerPolicy,
  DatasetSummary,
  EligibilityPage,
} from "../../api/types";
import { ConsumerReviewer } from "./ConsumerReviewer";

vi.mock("../../api/client", async (original) => ({
  ...(await original<typeof import("../../api/client")>()),
  api: {
    eligibilityPage: vi.fn(),
    reviewEligibility: vi.fn(),
    manualLabels: vi.fn(),
  },
}));
const policy: ConsumerPolicy = {
  version: "consumer-reactions-v2",
  start_date: "2026-09-09",
  end_date: "2026-09-10",
  timezone: "America/Chicago",
  per_author_limit: 2,
  reviewed_target: 500,
  duplicate_threshold: 0.9,
  selection_rule: "daily-quotas-recency;author-earliest-id",
};
const counts: ConsumerCounts = {
  retrieved: 1,
  unique_records: 1,
  screened_candidates: 1,
  pending_eligibility: 1,
  human_inclusions: 0,
  human_exclusions: 0,
  included: 0,
  author_cap_held: 0,
  missing_author: 0,
  pending_sentiment: 0,
  reviewed_final: 0,
  reviewed_target: 500,
  shortfall: 500,
  days: [],
};
const page: EligibilityPage = {
  total: 1,
  offset: 0,
  included_ids: [],
  counts,
  items: [
    {
      id: "one",
      text: "I hate this coffee maker",
      source_type: "x",
      created_at: "2026-09-09T07:00:00Z",
      author_id: "0001",
      comprehend_label: "positive",
      comprehend_confidence: 0.8,
      screening: {
        decision: "include",
        evidence: ["Personal reaction"],
        policy_version: "consumer-reactions-v2",
      },
    },
  ],
};
const dataset: DatasetSummary = {
  dataset_id: "consumer",
  source_type: "x",
  query: "coffee maker",
  record_count: 1,
  labelled_count: 0,
  truncated_reason: null,
  partial: false,
  preview: [],
};
const empty = { ...page, total: 0, items: [] };

beforeEach(() => {
  vi.mocked(api.eligibilityPage).mockResolvedValue(page);
  vi.mocked(api.reviewEligibility).mockResolvedValue(dataset);
});
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
async function start(extra: Partial<ComponentProps<typeof ConsumerReviewer>> = {}) {
  const view = render(
    <ConsumerReviewer
      datasetId="consumer"
      policy={policy}
      onBack={vi.fn()}
      onContinue={vi.fn()}
      {...extra}
    />,
  );
  await screen.findByText("I hate this coffee maker");
  return view;
}
function keep() {
  fireEvent.click(screen.getByRole("radio", { name: /Keep & label/ }));
  fireEvent.click(screen.getByRole("radio", { name: "negative" }));
}
const saveButton = () => screen.getByRole("button", { name: "Save and next →" });

it("refreshes accumulated totals and the queue after 439 saved posts become 530", async () => {
  const initial = { ...dataset, consumer_policy: policy, record_count: 439, candidate_target: 530, partial: true };
  vi.mocked(api.eligibilityPage).mockResolvedValue({ ...page, total: 439, counts: { ...counts, pending_eligibility: 439 } });
  const props = { datasetId: "consumer", policy, onBack: vi.fn(), onContinue: vi.fn() };
  const view = render(<ConsumerReviewer {...props} collection={initial} />);
  await screen.findByText("1 of 439 in this view");
  expect(screen.getByText("Total saved").nextElementSibling?.textContent === "439").toBeTruthy();
  view.rerender(<ConsumerReviewer {...props} collection={initial} collectionLoading />);
  expect(screen.getByLabelText("Loading review totals").getAttribute("aria-busy")).toBe("true");
  expect(screen.queryByText("I hate this coffee maker")).toBeNull();
  expect(screen.getByText("Total saved").nextElementSibling?.textContent === "439").toBeTruthy();
  vi.mocked(api.eligibilityPage).mockResolvedValue({ ...page, total: 530, counts: { ...counts, pending_eligibility: 530 } });
  view.rerender(<ConsumerReviewer {...props} collection={{ ...initial, record_count: 530, partial: false }} collectionMessage="Added 91 posts. 530 posts are saved in this dataset." />);
  await screen.findByText("1 of 530 in this view");
  expect(screen.getByText("Total saved").nextElementSibling?.textContent === "530").toBeTruthy();
  expect(screen.queryByText("1 of 439 in this view")).toBeNull();
  expect(screen.getByText("Added 91 posts. 530 posts are saved in this dataset.")).toBeTruthy();
});

it("hides outdated review counts and paid actions when refreshing the queue fails", async () => {
  const initial = { ...dataset, consumer_policy: policy, record_count: 439, candidate_target: 530, partial: true };
  const props = { datasetId: "consumer", policy, onBack: vi.fn(), onContinue: vi.fn(), onAdditional: vi.fn() };
  vi.mocked(api.eligibilityPage).mockResolvedValue({ ...page, total: 439 });
  const view = render(<ConsumerReviewer {...props} collection={initial} />);
  await screen.findByText("1 of 439 in this view");
  view.rerender(<ConsumerReviewer {...props} collection={initial} collectionLoading />);
  vi.mocked(api.eligibilityPage).mockRejectedValueOnce(new Error("Review refresh failed"));
  view.rerender(<ConsumerReviewer {...props} collection={{ ...initial, record_count: 530 }} />);
  await screen.findByText("Review refresh failed");
  expect(screen.getByText("Total saved").nextElementSibling?.textContent === "530").toBeTruthy();
  expect(screen.queryByText("1 of 439 in this view")).toBeNull();
  expect(screen.queryByText("Get more posts")).toBeNull();
  expect(screen.getByRole("button", { name: "Continue to Clean →" }).hasAttribute("disabled")).toBe(true);
  vi.mocked(api.eligibilityPage).mockResolvedValue({ ...page, total: 530 });
  fireEvent.click(screen.getByRole("button", { name: "Retry loading review" }));
  await screen.findByText("1 of 530 in this view");
  expect(props.onAdditional).not.toHaveBeenCalled();
});

it("keeps and labels in one request, advances the pending queue and hides automated sentiment", async () => {
  await start();
  expect(api.eligibilityPage).toHaveBeenCalledWith(
    "consumer",
    0,
    "needs_review",
    expect.any(AbortSignal),
  );
  expect(screen.queryByText(/Original automated suggestion/)).toBeNull();
  expect(saveButton().hasAttribute("disabled")).toBe(true);
  fireEvent.click(screen.getByRole("radio", { name: /Keep & label/ }));
  expect(saveButton().hasAttribute("disabled")).toBe(true);
  fireEvent.click(screen.getByRole("radio", { name: "negative" }));
  vi.mocked(api.eligibilityPage).mockResolvedValue(empty);
  fireEvent.click(saveButton());
  await screen.findByText("You’re caught up");
  expect(api.reviewEligibility).toHaveBeenCalledWith("consumer", [
    {
      id: "one",
      decision: "include",
      reason: null,
      note: "",
      label: "negative",
    },
  ]);
  expect(api.manualLabels).not.toHaveBeenCalled();
  expect(document.activeElement).toBe(screen.getByRole("heading", { name: "Review progress" }));
});

it("requires an exclusion reason and override note, retaining edits on failure", async () => {
  await start();
  fireEvent.click(screen.getByRole("radio", { name: /Exclude post/ }));
  expect(saveButton().hasAttribute("disabled")).toBe(true);
  fireEvent.change(screen.getByLabelText("Exclusion reason"), {
    target: { value: "news_or_article" },
  });
  expect(saveButton().hasAttribute("disabled")).toBe(true);
  fireEvent.change(screen.getByLabelText(/Why override/), {
    target: { value: "This was a headline quote" },
  });
  vi.mocked(api.reviewEligibility).mockRejectedValueOnce(new Error("offline"));
  fireEvent.click(saveButton());
  await screen.findByText(/Not saved/);
  expect((screen.getByLabelText(/Why override/) as HTMLTextAreaElement).value).toBe(
    "This was a headline quote",
  );
  vi.mocked(api.eligibilityPage).mockResolvedValue(empty);
  fireEvent.click(saveButton());
  await screen.findByText("You’re caught up");
  expect(api.reviewEligibility).toHaveBeenLastCalledWith("consumer", [
    {
      id: "one",
      decision: "exclude",
      reason: "news_or_article",
      note: "This was a headline quote",
      label: null,
    },
  ]);
});

it("protects unsaved choices and allows discarding them without changing saved data", async () => {
  const active = vi.fn();
  await start({ onReviewActiveChange: active });
  keep();
  expect(active).toHaveBeenLastCalledWith(true);
  expect(screen.getByLabelText("Show posts").hasAttribute("disabled")).toBe(true);
  expect(screen.getByRole("button", { name: "Continue to Clean →" }).hasAttribute("disabled")).toBe(
    true,
  );
  const event = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(event);
  expect(event.defaultPrevented).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Discard changes" }));
  expect(active).toHaveBeenLastCalledWith(false);
  expect(api.reviewEligibility).not.toHaveBeenCalled();
  expect(screen.getByLabelText("Show posts").hasAttribute("disabled")).toBe(false);
});

it("prevents duplicate saves, navigation and collection while a save is in flight", async () => {
  let resolve!: (value: DatasetSummary) => void;
  vi.mocked(api.reviewEligibility).mockReturnValue(
    new Promise((done) => {
      resolve = done;
    }),
  );
  const request = vi.fn();
  await start({
    collection: {
      ...dataset,
      consumer_policy: policy,
      candidate_target: 20,
      can_collect_more: true,
    },
    onAdditional: request,
  });
  keep();
  fireEvent.click(saveButton());
  expect(screen.getByRole("button", { name: "Continue to Clean →" }).hasAttribute("disabled")).toBe(
    true,
  );
  const additional = screen.getByRole("button", {
    name: "Get more posts",
  });
  expect(additional.hasAttribute("disabled")).toBe(true);
  fireEvent.click(additional);
  fireEvent.click(saveButton());
  expect(request).not.toHaveBeenCalled();
  expect(api.reviewEligibility).toHaveBeenCalledTimes(1);
  vi.mocked(api.eligibilityPage).mockResolvedValue(empty);
  await act(async () => resolve(dataset));
  await screen.findByText("You’re caught up");
});

it("restores an excluded post with a label in the same review form", async () => {
  vi.mocked(api.eligibilityPage)
    .mockResolvedValueOnce(empty)
    .mockResolvedValue({
      ...page,
      items: [
        {
          ...page.items[0],
          eligibility: "exclude",
          eligibility_reviewed: true,
          eligibility_reason: "off_topic",
        },
      ],
    });
  render(
    <ConsumerReviewer datasetId="consumer" policy={policy} onBack={vi.fn()} onContinue={vi.fn()} />,
  );
  await screen.findByText("You’re caught up");
  fireEvent.change(screen.getByLabelText("Show posts"), {
    target: { value: "exclude" },
  });
  await screen.findByText("I hate this coffee maker");
  keep();
  vi.mocked(api.eligibilityPage).mockResolvedValue(empty);
  fireEvent.click(saveButton());
  await waitFor(() =>
    expect(api.reviewEligibility).toHaveBeenCalledWith("consumer", [
      {
        id: "one",
        decision: "include",
        reason: null,
        note: "",
        label: "negative",
      },
    ]),
  );
});

it("does not skip a post when saving the last item shrinks a filtered queue", async () => {
  let rows = [page.items[0], { ...page.items[0], id: "two", text: "Another post" }];
  vi.mocked(api.eligibilityPage).mockImplementation(async (_id, offset) => ({
    ...page,
    total: rows.length,
    offset,
    items: rows.slice(offset, offset + 1),
  }));
  vi.mocked(api.reviewEligibility).mockImplementation(async (_id, items) => {
    rows = rows.filter((row) => row.id !== items[0].id);
    return dataset;
  });
  await start();
  fireEvent.click(screen.getByRole("button", { name: "Next without saving →" }));
  await screen.findByText("Another post");
  keep();
  fireEvent.click(saveButton());
  await screen.findByText("I hate this coffee maker");
  expect(api.eligibilityPage).toHaveBeenLastCalledWith(
    "consumer",
    0,
    "needs_review",
    expect.any(AbortSignal),
  );
});

it("opens new candidates in Needs review even when collecting from a saved-post view", async () => {
  const props = {
    datasetId: "consumer",
    policy,
    onBack: vi.fn(),
    onContinue: vi.fn(),
    collection: dataset,
  };
  const view = render(<ConsumerReviewer {...props} />);
  await screen.findByText("I hate this coffee maker");
  fireEvent.change(screen.getByLabelText("Show posts"), { target: { value: "include" } });
  await waitFor(() =>
    expect(api.eligibilityPage).toHaveBeenLastCalledWith(
      "consumer",
      0,
      "include",
      expect.any(AbortSignal),
    ),
  );
  view.rerender(<ConsumerReviewer {...props} collection={{ ...dataset, record_count: 2 }} />);
  await waitFor(() =>
    expect(api.eligibilityPage).toHaveBeenLastCalledWith(
      "consumer",
      0,
      "needs_review",
      expect.any(AbortSignal),
    ),
  );
  expect((screen.getByLabelText("Show posts") as HTMLSelectElement).value).toBe("needs_review");
});

it("ignores a late save response after this review has unmounted", async () => {
  let resolve!: (value: DatasetSummary) => void;
  vi.mocked(api.reviewEligibility).mockReturnValue(
    new Promise((done) => {
      resolve = done;
    }),
  );
  const changed = vi.fn();
  const view = await start({ onChanged: changed });
  keep();
  fireEvent.click(saveButton());
  view.unmount();
  await act(async () => resolve(dataset));
  expect(changed).not.toHaveBeenCalled();
});

it.each(["all", "include"])(
  "advances after saving a matching decision in the %s view",
  async (status) => {
    const rows = [page.items[0], { ...page.items[0], id: "two", text: "Another saved post" }];
    vi.mocked(api.eligibilityPage).mockImplementation(async (_id, offset) => ({
      ...page,
      total: 2,
      offset,
      items: rows.slice(offset, offset + 1),
    }));
    await start();
    fireEvent.change(screen.getByLabelText("Show posts"), { target: { value: status } });
    await waitFor(() =>
      expect(api.eligibilityPage).toHaveBeenLastCalledWith(
        "consumer",
        0,
        status,
        expect.any(AbortSignal),
      ),
    );
    await screen.findByText("I hate this coffee maker");
    keep();
    fireEvent.click(saveButton());
    await screen.findByText("Another saved post");
    expect(api.eligibilityPage).toHaveBeenLastCalledWith(
      "consumer",
      1,
      status,
      expect.any(AbortSignal),
    );
  },
);
