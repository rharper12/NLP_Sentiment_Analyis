// @vitest-environment jsdom
import { StrictMode, type ComponentProps } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import App from "./App";
import { api } from "./api/client";
import type { DatasetSummary, HealthResponse, PreprocessResponse, StepInfo } from "./api/types";
import type { Header } from "./components/Header";
import type { CollectStep } from "./components/stages/CollectStep";
import type { CleanStep } from "./components/stages/CleanStep";
import type { AnalyzeStep } from "./components/stages/AnalyzeStep";

vi.mock("./api/client", async (original) => ({ ...await original<typeof import("./api/client")>(), api: { health: vi.fn(), steps: vi.fn(), load: vi.fn(), preprocess: vi.fn() } }));
vi.mock("./components/Header", () => ({ Header: (p: ComponentProps<typeof Header>) => <output data-testid="spend">{p.spendVersion}</output> }));
vi.mock("./components/stages/CollectStep", () => ({ CollectStep: (p: ComponentProps<typeof CollectStep>) => <><output>{p.dataset?.dataset_id}</output><button onClick={() => p.onLoadSample(500)}>Load sample</button><button onClick={() => p.onSearch("test", 500, {})}>Search test</button><button onClick={p.onCancel}>Cancel collection</button><button onClick={p.onContinue}>Continue clean</button></> }));
vi.mock("./components/stages/CleanStep", () => ({ CleanStep: (p: ComponentProps<typeof CleanStep>) => <><output data-testid="config">{p.config.order.join(",")}</output><button onClick={p.onRun}>Process</button></> }));
vi.mock("./components/stages/AnalyzeStep", () => ({ AnalyzeStep: (p: ComponentProps<typeof AnalyzeStep>) => <><output data-testid="version">{p.runVersion}</output><output data-testid="result">{p.run?.dataset_id ?? "none"}</output><button onClick={p.onRerun}>Rerun</button></> }));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => { resolve = yes; });
  return { resolve, promise };
}
const dataset = (id: string): DatasetSummary => ({ dataset_id: id, source_type: "csv", query: null, record_count: 500, labelled_count: 0, truncated_reason: null, partial: false, preview: [] });
// These stage stubs only inspect dataset identity; the API's full shapes are covered in client tests.
const processed = (id: string) => ({ dataset_id: id }) as PreprocessResponse;

beforeEach(() => {
  localStorage.setItem("sentiment-prep.theme", "light");
  vi.mocked(api.health).mockResolvedValue({} as HealthResponse);
  vi.mocked(api.steps).mockResolvedValue([]);
});
afterEach(() => { cleanup(); vi.resetAllMocks(); });

async function enterAnalyze() {
  vi.mocked(api.load).mockResolvedValue(dataset("old"));
  fireEvent.click(screen.getByText("Load sample"));
  await screen.findByText("old");
  fireEvent.click(screen.getByText("Continue clean"));
  fireEvent.click(screen.getByText("Process"));
  expect(api.preprocess).toHaveBeenLastCalledWith(
    "old", [], expect.objectContaining({ keep_negations: false }), false, expect.any(AbortSignal),
  );
}

it("dataset replacement invalidates an old processing result and its version follow-up", async () => {
  const old = deferred<PreprocessResponse>();
  vi.mocked(api.preprocess).mockReturnValueOnce(old.promise).mockResolvedValue(processed("new"));
  render(<App />);
  await enterAnalyze();
  const signal = vi.mocked(api.preprocess).mock.calls[0][4]!;
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
  vi.mocked(api.preprocess).mockResolvedValueOnce(processed("old")).mockReturnValueOnce(rerun.promise);
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
  vi.mocked(api.steps).mockReturnValueOnce(old.promise).mockResolvedValueOnce([{ name: "current" } as StepInfo]);
  render(<StrictMode><App /></StrictMode>);
  vi.mocked(api.load).mockResolvedValue(dataset("current"));
  fireEvent.click(screen.getByText("Load sample"));
  await screen.findByText("current");
  fireEvent.click(screen.getByText("Continue clean"));
  expect(screen.getByTestId("config").textContent).toBe("current");
  await act(async () => old.resolve([{ name: "obsolete" } as StepInfo]));
  expect(screen.getByTestId("config").textContent).toBe("current");
});
