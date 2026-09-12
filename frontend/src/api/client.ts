// Thin fetch wrapper. Every call accepts an AbortSignal so the UI can cancel in-flight work,
// and every error carries the server's request id so it can be quoted when reading logs.

import type {
  CheckpointInfo,
  CheckpointList,
  CheckpointStage,
  DatasetSummary,
  HealthResponse,
  HistoryRun,
  LabelEstimate,
  LabelProgress,
  LabelSummary,
  PreprocessResponse,
  RecordPage,
  ReviewMode,
  ReviewPage,
  SampleUnit,
  SentimentLabel,
  SpendSummary,
  StepInfo,
  StepOptions,
} from "./types";

const BASE = import.meta.env.VITE_API_URL ?? "/api";
// Set at build time for deployments whose API requires a key. Never a user secret: it gates the
// operator's own budget, and the built bundle is only as private as where it is hosted.
const API_KEY = import.meta.env.VITE_API_KEY as string | undefined;

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly requestId: string | null,
  ) {
    super(message);
  }
}

export const isAbort = (error: unknown) =>
  error instanceof DOMException && error.name === "AbortError";

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (API_KEY) headers.set("X-API-Key", API_KEY);
  const response = await fetch(`${BASE}${path}`, { ...init, headers });
  const requestId = response.headers.get("X-Request-Id");
  if (!response.ok) {
    let message = response.statusText;
    try {
      const body = (await response.json()) as { error?: string; detail?: unknown };
      message = body.error ?? JSON.stringify(body.detail ?? body);
    } catch {
      /* non-JSON error body; keep statusText */
    }
    throw new ApiError(message, response.status, requestId);
  }
  return (await response.json()) as T;
}

const json = (body: unknown, signal?: AbortSignal): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
  signal,
});

export const api = {
  health: (signal?: AbortSignal) => request<HealthResponse>("/health", { signal }),
  steps: (signal?: AbortSignal) => request<StepInfo[]>("/steps", { signal }),

  load: (source: "x" | "huggingface", limit: number, query: string, signal?: AbortSignal) =>
    request<DatasetSummary>("/dataset/load", json({ source, limit, query: query || null }, signal)),

  upload: (file: File, signal?: AbortSignal) => {
    const form = new FormData();
    form.append("file", file);
    return request<DatasetSummary>("/dataset/upload", { method: "POST", body: form, signal });
  },

  preprocess: (
    datasetId: string,
    steps: string[],
    options: StepOptions,
    explain: boolean,
    signal?: AbortSignal,
  ) =>
    request<PreprocessResponse>(
      `/dataset/${datasetId}/preprocess`,
      json({ steps, options, explain }, signal),
    ),

  /** All records in one call; the grid holds them in memory and virtualises rows itself. */
  allRecords: async (datasetId: string, total: number, signal?: AbortSignal) => {
    const PAGE = 1000; // server caps a page at 1000
    const items: RecordPage["items"] = [];
    for (let offset = 0; offset < total; offset += PAGE) {
      const page = await request<RecordPage>(
        `/dataset/${datasetId}/records?offset=${offset}&limit=${PAGE}`,
        { signal },
      );
      items.push(...page.items);
      if (page.items.length < PAGE) break;
    }
    return items;
  },

  labelSummary: (datasetId: string, signal?: AbortSignal) =>
    request<LabelSummary>(`/dataset/${datasetId}/labels/summary`, { signal }),

  labelEstimate: (datasetId: string, signal?: AbortSignal) =>
    request<LabelEstimate>(`/dataset/${datasetId}/labels/estimate`, { signal }),

  /** One slice of Comprehend labelling; the caller loops until `done`. */
  labelComprehend: (datasetId: string, maxRecords: number, signal?: AbortSignal) =>
    request<LabelProgress>(
      `/dataset/${datasetId}/labels/comprehend`,
      json({ max_records: maxRecords, confirm_cost: true }, signal),
    ),

  chooseReview: (datasetId: string, mode: ReviewMode, size: number, unit: SampleUnit, signal?: AbortSignal) =>
    request<LabelSummary>(`/dataset/${datasetId}/labels/review`, {
      ...json({ mode, size, unit, seed: 7 }, signal),
      method: "PUT",
    }),

  reviewPage: (datasetId: string, offset: number, limit: number, signal?: AbortSignal) =>
    request<ReviewPage>(`/dataset/${datasetId}/labels/review?offset=${offset}&limit=${limit}`, { signal }),

  manualLabels: (datasetId: string, items: { id: string; label: SentimentLabel }[], signal?: AbortSignal) =>
    request<LabelSummary>(`/dataset/${datasetId}/labels/manual`, json({ items }, signal)),

  checkpoints: (datasetId: string, signal?: AbortSignal) =>
    request<CheckpointList>(`/dataset/${datasetId}/checkpoints`, { signal }),

  convertCheckpoint: (datasetId: string, stage: CheckpointStage, signal?: AbortSignal) =>
    request<CheckpointInfo>(`/dataset/${datasetId}/checkpoints/${stage}/parquet`, { method: "POST", signal }),

  save: (datasetId: string, signal?: AbortSignal) =>
    request<{ uri: string }>(`/dataset/${datasetId}/save`, { method: "POST", signal }),

  spend: (includeXUsage = false, signal?: AbortSignal) =>
    request<SpendSummary>(`/spend?include_x_usage=${includeXUsage}`, { signal }),

  history: (signal?: AbortSignal) => request<HistoryRun[]>("/history?limit=25", { signal }),

  exportUrl: (datasetId: string, kind: "csv" | "xlsx" | "parquet" | "md") =>
    kind === "md"
      ? `${BASE}/dataset/${datasetId}/report.md`
      : `${BASE}/dataset/${datasetId}/export.${kind}`,

};
