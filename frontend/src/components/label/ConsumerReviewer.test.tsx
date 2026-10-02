// @vitest-environment jsdom
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
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
let rows: EligibilityPage["items"];

beforeEach(() => {
  rows = [...page.items, { ...page.items[0], id: "two", text: "Another post" }];
  vi.mocked(api.eligibilityPage).mockImplementation(
    async (_id, requested, _status, _signal, startAt) => {
      const first = rows.findIndex(
        (row) =>
          !row.eligibility_reviewed ||
          (startAt === "first_unlabeled" &&
            row.eligibility === "include" &&
            !row.sentiment_reviewed),
      );
      const offset = startAt ? (first < 0 ? rows.length : first) : requested;
      return {
        ...page,
        total: rows.length,
        offset,
        items: rows.slice(offset, offset + 1),
      };
    },
  );
  vi.mocked(api.reviewEligibility).mockImplementation(
    async (_id, decisions) => {
      for (const choice of decisions) {
        rows = rows.map((row) =>
          row.id !== choice.id
            ? row
            : {
                ...row,
                eligibility: choice.decision,
                eligibility_reviewed: true,
                label: choice.label ?? row.label,
                sentiment_reviewed: !!choice.label,
              },
        );
      }
      return dataset;
    },
  );
});
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
function setup(extra: Partial<ComponentProps<typeof ConsumerReviewer>> = {}) {
  const props = {
    datasetId: "consumer",
    policy,
    onBack: vi.fn(),
    onContinue: vi.fn(),
    ...extra,
  };
  return { ...render(<ConsumerReviewer {...props} />), props };
}
async function optIn(sentiment = true) {
  fireEvent.click(screen.getByRole("button", { name: "Yes, review posts" }));
  fireEvent.click(
    screen.getByRole("button", {
      name: sentiment ? "Yes, review & label" : "No, review only",
    }),
  );
  await screen.findByText("I hate this coffee maker");
}
const button = (name: string) => screen.getByRole("button", { name });

it("asks before reviewing and skips without approving, labeling or loading posts", () => {
  const { props } = setup();
  expect(
    screen.getByRole("heading", {
      name: "Would you like to review each post?",
    }),
  ).toBeTruthy();
  expect(screen.queryByLabelText("Show posts")).toBeNull();
  fireEvent.click(button("Skip for now →"));
  expect(props.onContinue).toHaveBeenCalledExactlyOnceWith();
  expect(api.reviewEligibility).not.toHaveBeenCalled();
  expect(api.manualLabels).not.toHaveBeenCalled();
  expect(api.eligibilityPage).not.toHaveBeenCalled();
});

it("saves a sentiment in one request, advances, and revisits the saved card with Previous", async () => {
  setup();
  await optIn();
  expect(api.eligibilityPage).toHaveBeenCalledWith(
    "consumer",
    0,
    "all",
    expect.any(AbortSignal),
    "first_unlabeled",
  );
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(screen.queryByText(/Original automated suggestion/)).toBeNull();
  fireEvent.click(button("negative"));
  await screen.findByText("Another post");
  expect(api.reviewEligibility).toHaveBeenCalledExactlyOnceWith("consumer", [
    { id: "one", decision: "include", label: "negative" },
  ]);
  expect(api.manualLabels).not.toHaveBeenCalled();
  expect(document.activeElement).toBe(
    screen.getByRole("heading", { name: "Review this post" }),
  );
  fireEvent.click(button("← Previous"));
  await screen.findByText("I hate this coffee maker");
  expect(button("negative").getAttribute("aria-pressed")).toBe("true");
  fireEvent.click(button("neutral"));
  await screen.findByText("Another post");
  expect(api.reviewEligibility).toHaveBeenLastCalledWith("consumer", [
    { id: "one", decision: "include", label: "neutral" },
  ]);
});

it("supports review without labels and allows adding sentiment later", async () => {
  setup();
  await optIn(false);
  expect(screen.queryByRole("button", { name: "positive" })).toBeNull();
  expect(api.eligibilityPage).toHaveBeenLastCalledWith(
    "consumer",
    0,
    "all",
    expect.any(AbortSignal),
    "first_unreviewed",
  );
  fireEvent.click(button("Keep post"));
  await screen.findByText("Another post");
  expect(api.reviewEligibility).toHaveBeenLastCalledWith("consumer", [
    { id: "one", decision: "include" },
  ]);
  fireEvent.click(button("Review settings"));
  fireEvent.click(button("Yes, review & label"));
  await screen.findByText("I hate this coffee maker");
  expect(button("Keep without a label")).toBeTruthy();
  expect(api.manualLabels).not.toHaveBeenCalled();
});

it("excludes without a reason and restores the same post through stable navigation", async () => {
  setup();
  await optIn();
  fireEvent.click(button("Exclude post"));
  await screen.findByText("Another post");
  expect(api.reviewEligibility).toHaveBeenLastCalledWith("consumer", [
    { id: "one", decision: "exclude" },
  ]);
  fireEvent.click(button("← Previous"));
  await screen.findByText("Saved: excluded.");
  fireEvent.click(button("mixed"));
  await screen.findByText("Another post");
  expect(rows[0].eligibility).toBe("include");
  expect(rows[0].label).toBe("mixed");
});

it("navigates without saving and resumes the first unfinished post after reaching the end", async () => {
  setup();
  await optIn();
  fireEvent.click(button("Next →"));
  await screen.findByText("Another post");
  fireEvent.click(button("Next →"));
  await screen.findByText("End of posts");
  expect(api.reviewEligibility).not.toHaveBeenCalled();
  fireEvent.click(button("Finish remaining reviews"));
  await screen.findByText("I hate this coffee maker");
  expect(button("← Previous").hasAttribute("disabled")).toBe(true);
});

it.each(["Retry save", "Discard unsaved choice"])(
  "retains failed decisions until %s",
  async (action) => {
    const active = vi.fn();
    setup({ onReviewActiveChange: active });
    await optIn();
    vi.mocked(api.reviewEligibility).mockRejectedValueOnce(
      new Error("offline"),
    );
    fireEvent.click(button("negative"));
    await screen.findByText(/Not saved/);
    expect(button("negative").getAttribute("aria-pressed")).toBe("true");
    expect(active).toHaveBeenLastCalledWith(true);
    expect(button("Next →").hasAttribute("disabled")).toBe(true);
    expect(button("Continue to Clean →").hasAttribute("disabled")).toBe(true);
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
    fireEvent.click(button(action));
    if (action === "Retry save") {
      await screen.findByText("Another post");
      expect(api.reviewEligibility).toHaveBeenCalledTimes(2);
    } else {
      expect(screen.getByText("I hate this coffee maker")).toBeTruthy();
      expect(api.reviewEligibility).toHaveBeenCalledTimes(1);
      expect(rows[0].sentiment_reviewed).toBeFalsy();
    }
    expect(active).toHaveBeenLastCalledWith(false);
  },
);

it("prevents duplicate saves, navigation, and paid collection during a save", async () => {
  let resolve!: (value: DatasetSummary) => void;
  vi.mocked(api.reviewEligibility).mockReturnValue(
    new Promise((done) => {
      resolve = done;
    }),
  );
  const request = vi.fn();
  setup({
    collection: {
      ...dataset,
      consumer_policy: policy,
      partial: true,
      candidate_target: 20,
    },
    onAdditional: request,
  });
  await optIn();
  fireEvent.click(button("negative"));
  fireEvent.click(button("negative"));
  expect(api.reviewEligibility).toHaveBeenCalledTimes(1);
  expect(button("Continue to Clean →").hasAttribute("disabled")).toBe(true);
  expect(button("Get more posts").hasAttribute("disabled")).toBe(true);
  fireEvent.click(button("Get more posts"));
  expect(request).not.toHaveBeenCalled();
  await act(async () => resolve(dataset));
  await screen.findByText("Another post");
});

it("refreshes 439 saved posts to 530 and hides stale cards during collection", async () => {
  const initial = {
    ...dataset,
    consumer_policy: policy,
    record_count: 439,
    candidate_target: 530,
    partial: true,
  };
  vi.mocked(api.eligibilityPage).mockResolvedValue({ ...page, total: 439 });
  const view = setup({ collection: initial });
  await optIn();
  expect(screen.getByText("Post 1 of 439")).toBeTruthy();
  view.rerender(<ConsumerReviewer {...view.props} collectionLoading />);
  expect(screen.queryByText("I hate this coffee maker")).toBeNull();
  expect(screen.getByText("Loading your review queue…")).toBeTruthy();
  vi.mocked(api.eligibilityPage).mockResolvedValue({ ...page, total: 530 });
  view.rerender(
    <ConsumerReviewer
      {...view.props}
      collection={{ ...initial, record_count: 530 }}
    />,
  );
  await screen.findByText("Post 1 of 530");
  expect(screen.queryByText("Post 1 of 439")).toBeNull();
});

it("allows retry or leaving after a failed load, without stale totals or paid actions", async () => {
  const initial = {
    ...dataset,
    consumer_policy: policy,
    record_count: 439,
    candidate_target: 530,
    partial: true,
  };
  const view = setup({ collection: initial, onAdditional: vi.fn() });
  await optIn();
  vi.mocked(api.eligibilityPage).mockRejectedValueOnce(
    new Error("Review refresh failed"),
  );
  view.rerender(
    <ConsumerReviewer
      {...view.props}
      collection={{ ...initial, record_count: 530 }}
    />,
  );
  await screen.findByText("Review refresh failed");
  expect(screen.queryByText("I hate this coffee maker")).toBeNull();
  expect(screen.queryByRole("button", { name: "Get more posts" })).toBeNull();
  expect(button("Continue to Clean →").hasAttribute("disabled")).toBe(true);
  expect(button("← Back to Collect").hasAttribute("disabled")).toBe(false);
  vi.mocked(api.eligibilityPage).mockResolvedValue({ ...page, total: 530 });
  fireEvent.click(button("Retry loading review"));
  await screen.findByText("Post 1 of 530");
});

it("ignores a late save response after unmount", async () => {
  let resolve!: (value: DatasetSummary) => void;
  vi.mocked(api.reviewEligibility).mockReturnValue(
    new Promise((done) => {
      resolve = done;
    }),
  );
  const changed = vi.fn();
  const view = setup({ onChanged: changed });
  await optIn();
  fireEvent.click(button("negative"));
  view.unmount();
  await act(async () => resolve(dataset));
  expect(changed).not.toHaveBeenCalled();
});
