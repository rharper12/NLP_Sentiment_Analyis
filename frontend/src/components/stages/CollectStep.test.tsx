// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { CollectStep } from "./CollectStep";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

it("requires the user's topic and dates instead of inserting product-specific defaults", () => {
  const search = vi.fn();
  render(
    <CollectStep
      busy={false}
      error={null}
      xConfigured
      dataset={null}
      onSearch={search}
      onLoadSample={vi.fn()}
      onUpload={vi.fn()}
      onRestore={vi.fn()}
      onCancel={vi.fn()}
      onContinue={vi.fn()}
    />,
  );
  const mode = screen.getByRole("combobox", { name: "Collection option" });
  expect(screen.getByRole("option", { name: "General search — sentiment labeling" })).toBeTruthy();
  expect(
    screen.getByRole("option", { name: "Consumer reactions — include/exclude + sentiment" }),
  ).toBeTruthy();
  expect(document.getElementById(mode.getAttribute("aria-describedby")!)?.textContent).toContain(
    "Collect matching posts, clean the text, then label sentiment.",
  );
  fireEvent.change(mode, { target: { value: "consumer" } });
  expect((screen.getByLabelText("Topic") as HTMLInputElement).value).toBe("");
  expect((screen.getByLabelText("From") as HTMLInputElement).value).toBe("");
  expect((screen.getByLabelText("To") as HTMLInputElement).value).toBe("");
  const timezone = screen.getByRole("combobox", { name: "Timezone" }) as HTMLSelectElement;
  expect(timezone.value).toBe(Intl.DateTimeFormat().resolvedOptions().timeZone);
  expect([...timezone.options].map((option) => option.value)).toEqual(
    expect.arrayContaining(["UTC", "America/Chicago", "Europe/London", "Asia/Tokyo"]),
  );
  expect(screen.getByRole("button", { name: "Search and collect" }).hasAttribute("disabled")).toBe(
    true,
  );
  fireEvent.change(screen.getByLabelText("Topic"), { target: { value: '"coffee maker"' } });
  fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-09-09" } });
  fireEvent.change(screen.getByLabelText("To"), { target: { value: "2026-09-10" } });
  fireEvent.change(screen.getByLabelText("Timezone"), { target: { value: "America/Chicago" } });
  fireEvent.change(screen.getByLabelText("Posts to collect"), {
    target: { value: "50" },
  });
  timezone.focus();
  fireEvent.keyDown(timezone, { key: "Enter" });
  fireEvent.submit(timezone.closest("form")!);
  expect(search).not.toHaveBeenCalled();
  fireEvent.keyUp(timezone, { key: "Enter" });
  fireEvent.click(screen.getByRole("button", { name: "Search and collect" }));
  expect(search).toHaveBeenCalledWith('"coffee maker"', 50, {
    start_date: "2026-09-09",
    end_date: "2026-09-10",
    timezone: "America/Chicago",
    preset: "consumer_reactions",
    per_author_limit: 2,
    reviewed_target: 500,
  });
});

it("preserves an existing query, custom dates and quota when changing collection options", () => {
  render(
    <CollectStep
      busy={false}
      error={null}
      xConfigured
      dataset={null}
      onSearch={vi.fn()}
      onLoadSample={vi.fn()}
      onUpload={vi.fn()}
      onRestore={vi.fn()}
      onCancel={vi.fn()}
      onContinue={vi.fn()}
    />,
  );
  fireEvent.change(screen.getByLabelText("Topic"), {
    target: { value: '"running shoes" -has:links' },
  });
  fireEvent.click(screen.getByRole("button", { name: "Custom" }));
  fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-08-01" } });
  fireEvent.change(screen.getByLabelText("To"), { target: { value: "2026-08-02" } });
  fireEvent.change(screen.getByLabelText("Timezone"), { target: { value: "Europe/London" } });
  fireEvent.change(screen.getByLabelText("How many posts"), { target: { value: "200" } });
  fireEvent.change(screen.getByLabelText("Collection option"), { target: { value: "consumer" } });
  expect((screen.getByLabelText("Topic") as HTMLInputElement).value).toBe(
    '"running shoes" -has:links',
  );
  expect((screen.getByLabelText("From") as HTMLInputElement).value).toBe("2026-08-01");
  expect((screen.getByLabelText("To") as HTMLInputElement).value).toBe("2026-08-02");
  expect((screen.getByLabelText("Timezone") as HTMLSelectElement).value).toBe("Europe/London");
  expect(
    (screen.getByLabelText("Posts to collect") as HTMLInputElement).value,
  ).toBe("200");
});

it("keeps the browser's valid local timezone when it is absent from the primary-zone list", () => {
  const options = Intl.DateTimeFormat().resolvedOptions();
  vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockReturnValue({
    ...options,
    timeZone: "Etc/GMT+5",
  });
  render(
    <CollectStep
      busy={false}
      error={null}
      xConfigured
      dataset={null}
      onSearch={vi.fn()}
      onLoadSample={vi.fn()}
      onUpload={vi.fn()}
      onRestore={vi.fn()}
      onCancel={vi.fn()}
      onContinue={vi.fn()}
    />,
  );
  fireEvent.change(screen.getByLabelText("Collection option"), { target: { value: "consumer" } });
  expect((screen.getByRole("combobox", { name: "Timezone" }) as HTMLSelectElement).value).toBe(
    "Etc/GMT+5",
  );
});

it("requires completed custom days and sends timezone-aware calendar input", () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-21T12:00:00Z"));
  const search = vi.fn();
  render(
    <CollectStep
      busy={false}
      error={null}
      xConfigured
      dataset={null}
      onSearch={search}
      onLoadSample={vi.fn()}
      onUpload={vi.fn()}
      onRestore={vi.fn()}
      onCancel={vi.fn()}
      onContinue={vi.fn()}
    />,
  );
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
  expect(submit.disabled).toBe(true);
  expect(screen.getByText(/completed days before today/)).toBeTruthy();
  fireEvent.change(to, { target: { value: "2026-09-20" } });
  expect(submit.disabled).toBe(false);
  fireEvent.click(submit);
  expect(search).toHaveBeenCalledWith('"iPhone Duo"', 600, {
    start_date: "2026-09-09",
    end_date: "2026-09-20",
    timezone: "UTC",
  });
});

it("offers an explicit resume action for saved partial collection", () => {
  const resume = vi.fn();
  render(
    <CollectStep
      busy={false}
      error={null}
      xConfigured
      dataset={{
        dataset_id: "x-stable",
        source_type: "x",
        query: "topic",
        record_count: 100,
        labelled_count: 0,
        truncated_reason: "request budget reached",
        partial: true,
        preview: [],
      }}
      onSearch={vi.fn()}
      onLoadSample={vi.fn()}
      onUpload={vi.fn()}
      onRestore={vi.fn()}
      onCancel={vi.fn()}
      onContinue={vi.fn()}
      onResume={resume}
    />,
  );
  fireEvent.click(screen.getByText("Get more posts"));
  expect(resume).toHaveBeenCalledOnce();
  expect(screen.getByText(/Collection stopped/).textContent).toContain("request budget reached");
});

it("uses the server rate for preflight and committed reads for the result", () => {
  render(
    <CollectStep
      busy={false}
      error={null}
      xConfigured
      costPerRead={0.017}
      dataset={{
        dataset_id: "x",
        source_type: "x",
        query: "test",
        record_count: 20,
        billed_reads: 100,
        committed_cost_usd: 1.7,
        labelled_count: 0,
        truncated_reason: null,
        partial: false,
        preview: [],
      }}
      onSearch={vi.fn()}
      onLoadSample={vi.fn()}
      onUpload={vi.fn()}
      onRestore={vi.fn()}
      onCancel={vi.fn()}
      onContinue={vi.fn()}
    />,
  );
  expect(screen.getByText("$10.20")).toBeTruthy();
  expect(screen.getByText("Total saved").nextElementSibling?.textContent).toBe("20");
  expect(screen.getByText(/100 provider reads/).textContent).toContain(
    "$1.70 estimated total cost",
  );
});

it("hides the local picker outside local file-backed development", () => {
  render(
    <CollectStep
      busy={false}
      error={null}
      xConfigured
      dataset={null}
      onSearch={vi.fn()}
      onLoadSample={vi.fn()}
      onUpload={vi.fn()}
      onRestore={vi.fn()}
      onCancel={vi.fn()}
      onContinue={vi.fn()}
    />,
  );
  expect(screen.queryByRole("tab", { name: "Saved datasets" })).toBeNull();
});

it("supports arrow-key navigation between source tabs", () => {
  render(
    <CollectStep
      busy={false}
      error={null}
      xConfigured
      dataset={null}
      onSearch={vi.fn()}
      onLoadSample={vi.fn()}
      onUpload={vi.fn()}
      onRestore={vi.fn()}
      onCancel={vi.fn()}
      onContinue={vi.fn()}
    />,
  );
  fireEvent.keyDown(screen.getByRole("tab", { name: "Search X" }), { key: "End" });
  expect(screen.getByRole("tab", { name: "Upload CSV" }).getAttribute("aria-selected")).toBe(
    "true",
  );
  expect(screen.getByRole("button", { name: "Import CSV" }).hasAttribute("disabled")).toBe(true);
  expect(document.activeElement).toBe(screen.getByRole("tab", { name: "Upload CSV" }));
});

it("keeps first-request, added, total and remaining counts distinct through a resume", () => {
  const props = {
    busy: false, error: null, xConfigured: true, onSearch: vi.fn(), onLoadSample: vi.fn(),
    onUpload: vi.fn(), onRestore: vi.fn(), onCancel: vi.fn(), onContinue: vi.fn(), onAdditional: vi.fn(),
  };
  const dataset = {
    dataset_id: "x-counts", source_type: "x" as const, query: "coffee", record_count: 350,
    labelled_count: 0, truncated_reason: "request budget reached", partial: true,
    candidate_target: 520, first_batch_saved: 350, last_batch_saved: 350,
    consumer_policy: { version: "consumer-reactions-v2" as const, start_date: "2026-09-09", end_date: "2026-09-11", timezone: "America/Chicago", per_author_limit: 2, reviewed_target: 500, duplicate_threshold: 0.9, selection_rule: "daily-quotas-recency;author-earliest-id" as const },
    consumer_counts: { retrieved: 350, unique_records: 350, screened_candidates: 350, pending_eligibility: 350, human_inclusions: 0, human_exclusions: 0, included: 0, author_cap_held: 0, missing_author: 0, pending_sentiment: 0, reviewed_final: 0, reviewed_target: 500, shortfall: 500, days: [] },
    preview: [{ id: "example", source_type: "x" as const, text: "A tweet preview that belongs in review" }],
  };
  const view = render(<CollectStep {...props} dataset={null} />);
  view.rerender(<CollectStep {...props} dataset={dataset} />);
  const count = (label: string) => screen.getByText(label).nextElementSibling?.textContent;
  expect(count("Total saved")).toBe("350");
  expect(count("Added last request")).toBe("350");
  expect(count("Still to collect")).toBe("170");
  expect(screen.getByText(/First request: 350 saved/)).toBeTruthy();
  expect(screen.queryByText("Needs review")).toBeNull();
  expect(screen.queryByText(/A tweet preview/)).toBeNull();
  expect(screen.queryByText(/reviewed posts needed/)).toBeNull();
  expect(screen.getByText("Start another collection").parentElement?.hasAttribute("open")).toBe(false);
  expect(document.activeElement).toBe(screen.getByRole("heading", { name: "Collect your dataset" }));
  fireEvent.click(screen.getByRole("button", { name: "Get more posts" }));
  expect(props.onAdditional).toHaveBeenCalledExactlyOnceWith(520);
  view.rerender(<CollectStep {...props} busy dataset={dataset} />);
  expect(count("Total saved")).toBe("350");
  expect(count("Added last request")).not.toBe("350");
  expect(screen.getByRole("button", { name: "Get more posts" }).hasAttribute("disabled")).toBe(true);
  expect(screen.getByRole("button", { name: /Continue to Review/ }).hasAttribute("disabled")).toBe(true);
  view.rerender(<CollectStep {...props} dataset={{ ...dataset, record_count: 530, last_batch_saved: 180, partial: false, truncated_reason: null }} />);
  expect(count("Total saved")).toBe("530");
  expect(count("Added last request")).toBe("180");
  expect(count("Still to collect")).toBe("0");
  expect(screen.getByText(/First request: 350 saved/)).toBeTruthy();
  expect(screen.getByRole("progressbar").getAttribute("value")).toBe("520");
  expect(screen.queryByRole("button", { name: "Get more posts" })).toBeNull();
  expect(props.onSearch).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: /Continue to Review/ }));
  expect(props.onContinue).toHaveBeenCalledOnce();
});

it("does not invent batch counts for older datasets and distinguishes zero from unknown", () => {
  const props = { busy: false, error: null, xConfigured: true, onSearch: vi.fn(), onLoadSample: vi.fn(), onUpload: vi.fn(), onRestore: vi.fn(), onCancel: vi.fn(), onContinue: vi.fn() };
  const dataset = { dataset_id: "old", source_type: "x" as const, query: "topic", record_count: 350, labelled_count: 0, truncated_reason: null, partial: true, preview: [], candidate_target: 500 };
  const view = render(<CollectStep {...props} dataset={dataset} />);
  expect(screen.getByText("Added last request").nextElementSibling?.textContent).toBe("—Not recorded");
  expect(screen.queryByText(/First request: 350/)).toBeNull();
  expect(screen.getByText(/first request count was not recorded/)).toBeTruthy();
  view.rerender(<CollectStep {...props} dataset={{ ...dataset, last_batch_saved: 0 }} />);
  expect(screen.getByText("Added last request").nextElementSibling?.textContent).toBe("0");
});
