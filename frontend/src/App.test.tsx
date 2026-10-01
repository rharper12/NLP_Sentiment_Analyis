// @vitest-environment jsdom
import { StrictMode, type ComponentProps } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import App from "./App";
import { api } from "./api/client";
import type {
  DatasetSummary,
  EligibilityPage,
  HealthResponse,
  PreprocessResponse,
  StepInfo,
} from "./api/types";
import type { Header } from "./components/Header";
import type { CollectStep } from "./components/stages/CollectStep";
import type { CleanStep } from "./components/stages/CleanStep";
import type { AnalyzeStep } from "./components/stages/AnalyzeStep";

vi.mock("./api/client", async (original) => ({
  ...(await original<typeof import("./api/client")>()),
  api: {
    health: vi.fn(),
    steps: vi.fn(),
    load: vi.fn(),
    dataset: vi.fn(),
    restoreLocal: vi.fn(),
    preprocess: vi.fn(),
    collectCandidates: vi.fn(),
    eligibilityPage: vi.fn(),
  },
}));
vi.mock("./components/Header", () => ({
  Header: (p: ComponentProps<typeof Header>) => (
    <output data-testid="spend">{p.spendVersion}</output>
  ),
}));
vi.mock("./components/stages/CollectStep", () => ({
  CollectStep: (p: ComponentProps<typeof CollectStep>) => (
    <>
      <output>{p.dataset?.dataset_id}</output>
      <button onClick={() => p.onLoadSample(500)}>Load sample</button>
      <button onClick={() => p.onSearch("test", 500, {})}>Search test</button>
      {p.onResume && <button onClick={p.onResume}>Resume collection</button>}
      <button onClick={p.onCancel}>Cancel collection</button>
      <button onClick={() => p.onRestore("saved-id")}>Restore original</button>
      <button onClick={p.onContinue}>Continue clean</button>
    </>
  ),
}));
vi.mock("./components/stages/CleanStep", () => ({
  CleanStep: (p: ComponentProps<typeof CleanStep>) => (
    <>
      <output data-testid="config">{p.config.order.join(",")}</output>
      <button onClick={p.onRun}>Process</button>
    </>
  ),
}));
vi.mock("./components/stages/AnalyzeStep", () => ({
  AnalyzeStep: (p: ComponentProps<typeof AnalyzeStep>) => (
    <>
      <output data-testid="version">{p.runVersion}</output>
      <output data-testid="result">{p.run?.dataset_id ?? "none"}</output>
      <button onClick={p.onRerun}>Rerun</button>
    </>
  ),
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
  });
  return { resolve, promise };
}
const dataset = (id: string): DatasetSummary => ({
  dataset_id: id,
  source_type: "csv",
  query: null,
  record_count: 500,
  labelled_count: 0,
  truncated_reason: null,
  partial: false,
  preview: [],
});
// These stage stubs only inspect dataset identity; the API's full shapes are covered in client tests.
const processed = (id: string) => ({ dataset_id: id }) as PreprocessResponse;

it("manually requests the review shortfall on the same dataset and returns to review without looping", async () => {
  const counts = {
    retrieved: 500,
    unique_records: 500,
    screened_candidates: 500,
    pending_eligibility: 0,
    human_inclusions: 350,
    human_exclusions: 150,
    included: 350,
    author_cap_held: 0,
    missing_author: 0,
    pending_sentiment: 0,
    reviewed_final: 350,
    reviewed_target: 500,
    shortfall: 150,
    days: [],
  };
  const consumer: DatasetSummary = {
    ...dataset("consumer"),
    source_type: "x",
    candidate_target: 500,
    can_collect_more: true,
    consumer_counts: counts,
    consumer_policy: {
      version: "consumer-reactions-v1",
      start_date: "2026-09-09",
      end_date: "2026-09-10",
      timezone: "America/Chicago",
      per_author_limit: 2,
      reviewed_target: 500,
      duplicate_threshold: 0.9,
      selection_rule: "daily-quotas-recency;author-earliest-id",
    },
  };
  const review: EligibilityPage = { total: 0, offset: 0, items: [], included_ids: [], counts };
  vi.mocked(api.load).mockResolvedValue(consumer);
  vi.mocked(api.eligibilityPage).mockResolvedValue(review);
  const pending = deferred<DatasetSummary>();
  vi.mocked(api.collectCandidates).mockReturnValue(pending.promise);
  render(<App />);
  fireEvent.click(screen.getByText("Search test"));
  await screen.findByText("consumer");
  fireEvent.click(screen.getByRole("button", { name: /Label/ }));
  await screen.findByRole("button", { name: "Collect additional candidates" });
  expect(api.collectCandidates).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Collect additional candidates" }));
  expect(api.collectCandidates).toHaveBeenCalledWith("consumer", 650, expect.any(AbortSignal));
  expect(screen.queryByRole("heading", { name: "Review consumer reactions" })).toBeNull();
  expect(screen.getByRole("button", { name: /Label/ }).hasAttribute("disabled")).toBe(true);
  vi.mocked(api.eligibilityPage).mockResolvedValue({
    ...review,
    counts: { ...counts, pending_eligibility: 150 },
  });
  await act(async () => pending.resolve({ ...consumer, candidate_target: 650, record_count: 650 }));
  await screen.findByText(/150 eligibility reviews/);
  expect(screen.getByRole("heading", { name: "Review consumer reactions" })).toBeTruthy();
  expect((screen.getByLabelText("Additional candidate quota") as HTMLInputElement).value).toBe(
    "150",
  );
  expect(api.load).toHaveBeenCalledTimes(1);
  expect(api.collectCandidates).toHaveBeenCalledTimes(1);
  vi.mocked(api.dataset).mockResolvedValue(consumer);
  fireEvent.click(screen.getByRole("button", { name: "Continue to Export →" }));
  await waitFor(() =>
    expect(document.activeElement).toBe(
      screen.getByRole("heading", { name: "Export" }),
    ),
  );
  fireEvent.click(screen.getByRole("button", { name: "← Back to Label" }));
  await waitFor(() =>
    expect(document.activeElement).toBe(
      screen.getByRole("heading", { name: "Review consumer reactions" }),
    ),
  );
});

it("keeps the stable request for a 499-post budget pause and removes resume after reaching 500", async () => {
  vi.mocked(api.load)
    .mockResolvedValueOnce({
      ...dataset("paused"),
      source_type: "x",
      record_count: 499,
      partial: true,
      truncated_reason: "request budget reached; resume to continue",
      retry_at: null,
    })
    .mockResolvedValueOnce({ ...dataset("finished"), source_type: "x" });
  render(<App />);
  fireEvent.click(screen.getByText("Search test"));
  await screen.findByText("paused");
  const requestId = vi.mocked(api.load).mock.calls[0][5];
  expect(requestId).toBeTruthy();
  fireEvent.click(screen.getByText("Resume collection"));
  await screen.findByText("finished");
  expect(api.load).toHaveBeenLastCalledWith(
    "x",
    500,
    "test",
    {},
    expect.any(AbortSignal),
    requestId,
  );
  expect(screen.queryByText("Resume collection")).toBeNull();
});

it("does not offer resume for a terminal provider query rejection", async () => {
  vi.mocked(api.load).mockResolvedValueOnce({
    ...dataset("rejected"),
    source_type: "x",
    record_count: 499,
    partial: false,
    truncated_reason:
      "X rejected the query. (X API returned HTTP 400); correct the request and start a new search",
  });
  render(<App />);
  fireEvent.click(screen.getByText("Search test"));
  await screen.findByText("rejected");
  expect(screen.queryByText("Resume collection")).toBeNull();
});

beforeEach(() => {
  localStorage.setItem("sentiment-prep.theme", "light");
  vi.mocked(api.health).mockResolvedValue({} as HealthResponse);
  vi.mocked(api.steps).mockResolvedValue([]);
});
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

async function enterAnalyze() {
  vi.mocked(api.load).mockResolvedValue(dataset("old"));
  fireEvent.click(screen.getByText("Load sample"));
  await screen.findByText("old");
  fireEvent.click(screen.getByText("Continue clean"));
  fireEvent.click(screen.getByText("Process"));
  expect(api.preprocess).toHaveBeenLastCalledWith(
    "old",
    [],
    expect.objectContaining({ keep_negations: false }),
    expect.any(AbortSignal),
  );
}

it("dataset replacement invalidates an old processing result and its version follow-up", async () => {
  const old = deferred<PreprocessResponse>();
  vi.mocked(api.preprocess).mockReturnValueOnce(old.promise).mockResolvedValue(processed("new"));
  render(<App />);
  await enterAnalyze();
  const signal = vi.mocked(api.preprocess).mock.calls[0][3]!;
  fireEvent.click(screen.getByRole("button", { name: /Collect/ }));
  vi.mocked(api.load).mockResolvedValue(dataset("new"));
  fireEvent.click(screen.getByText("Load sample"));
  await screen.findByText("new");
  expect(signal.aborted).toBe(true);
  await act(async () => old.resolve(processed("obsolete")));
  expect(screen.queryByTestId("result")).toBeNull();
  fireEvent.click(screen.getByText("Continue clean"));
  fireEvent.click(screen.getByText("Process"));
  await waitFor(() => expect(screen.getByTestId("result").textContent).toBe("new"));
  expect(screen.getByTestId("version").textContent).toBe("3");
});

it("blocks Label and Export navigation while an existing result is being rerun", async () => {
  const rerun = deferred<PreprocessResponse>();
  vi.mocked(api.preprocess)
    .mockResolvedValueOnce(processed("old"))
    .mockReturnValueOnce(rerun.promise);
  render(<App />);
  await enterAnalyze();
  await waitFor(() => expect(screen.getByTestId("result").textContent).toBe("old"));
  expect(screen.getByRole("button", { name: /Label/ }).hasAttribute("disabled")).toBe(false);
  fireEvent.click(screen.getByText("Rerun"));
  expect(screen.getByRole("button", { name: /Label/ }).hasAttribute("disabled")).toBe(true);
  expect(screen.getByRole("button", { name: /Export/ }).hasAttribute("disabled")).toBe(true);
  await act(async () => rerun.resolve(processed("rerun")));
  expect(screen.getByRole("button", { name: /Label/ }).hasAttribute("disabled")).toBe(false);
});

it("cancelled collection cannot restore a dataset or increment spend", async () => {
  const old = deferred<DatasetSummary>();
  vi.mocked(api.load).mockReturnValue(old.promise);
  render(<App />);
  fireEvent.click(screen.getByText("Search test"));
  fireEvent.click(screen.getByText("Cancel collection"));
  await act(async () => old.resolve({ ...dataset("obsolete"), source_type: "x" }));
  expect(screen.queryByText("obsolete")).toBeNull();
  expect(screen.getByTestId("spend").textContent).toBe("0");
  expect(screen.getByRole("button", { name: /Clean/ }).hasAttribute("disabled")).toBe(true);
});

it("Strict Mode's obsolete steps response cannot reinitialize pipeline configuration", async () => {
  const old = deferred<StepInfo[]>();
  vi.mocked(api.steps)
    .mockReturnValueOnce(old.promise)
    .mockResolvedValueOnce([{ name: "current" } as StepInfo]);
  render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
  vi.mocked(api.load).mockResolvedValue(dataset("current"));
  fireEvent.click(screen.getByText("Load sample"));
  await screen.findByText("current");
  fireEvent.click(screen.getByText("Continue clean"));
  expect(screen.getByTestId("config").textContent).toBe("current");
  await act(async () => old.resolve([{ name: "obsolete" } as StepInfo]));
  expect(screen.getByTestId("config").textContent).toBe("current");
});

it("restoring originals opens Clean without collection or processing calls", async () => {
  vi.mocked(api.restoreLocal).mockResolvedValue(dataset("restored"));
  render(<App />);
  fireEvent.click(screen.getByText("Restore original"));
  await screen.findByText("Process");
  expect(api.load).not.toHaveBeenCalled();
  expect(api.preprocess).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("Process"));
  expect(api.preprocess).toHaveBeenCalledWith(
    "restored",
    [],
    expect.objectContaining({ keep_negations: false }),
    expect.any(AbortSignal),
  );
});

it("failed restore stays on Collect", async () => {
  vi.mocked(api.restoreLocal).mockRejectedValue(new Error("Invalid saved dataset"));
  render(<App />);
  fireEvent.click(screen.getByText("Restore original"));
  await waitFor(() => expect(api.restoreLocal).toHaveBeenCalledOnce());
  expect(screen.queryByText("Process")).toBeNull();
  expect(screen.getByText("Restore original")).toBeTruthy();
});

it("a cancelled restore cannot advance to Clean when its response arrives", async () => {
  const pending = deferred<DatasetSummary>();
  vi.mocked(api.restoreLocal).mockReturnValue(pending.promise);
  render(<App />);
  fireEvent.click(screen.getByText("Restore original"));
  fireEvent.click(screen.getByText("Cancel collection"));
  await act(async () => pending.resolve(dataset("obsolete")));
  expect(screen.queryByText("Process")).toBeNull();
  expect(screen.queryByText("obsolete")).toBeNull();
});
