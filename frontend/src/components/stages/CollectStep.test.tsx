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
    "Choose this for manual relevance review",
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
  fireEvent.change(screen.getByLabelText("Candidate target (pilot: 50–100)"), {
    target: { value: "50" },
  });
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
    (screen.getByLabelText("Candidate target (pilot: 50–100)") as HTMLInputElement).value,
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
  fireEvent.click(screen.getByText("Resume collection"));
  expect(resume).toHaveBeenCalledOnce();
  expect(screen.getByText(/Stopped early/).textContent).toContain("request budget reached");
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
  expect(screen.getByText(/posts collected/).textContent).toContain("20");
  expect(screen.getByText(/100 accounted provider reads/).textContent).toContain(
    "estimated spend $1.70",
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
