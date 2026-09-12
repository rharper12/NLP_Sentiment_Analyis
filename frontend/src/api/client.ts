// Thin fetch wrapper. Every call accepts an AbortSignal so the UI can cancel in-flight work,
// every response is validated against a Zod schema before it reaches a component, and every
// error carries the server's request id so it can be quoted when reading logs.

import { z } from "zod";

import type {
  CheckpointStage,
  RecordPage,
  ReviewMode,
  SampleUnit,
  SentimentLabel,
  StepOptions,
} from "./types";
import {
  checkpointInfoSchema,
  checkpointListSchema,
  datasetSummarySchema,
  healthSchema,
  historyRunSchema,
  labelEstimateSchema,
  labelProgressSchema,
  labelSummarySchema,
  preprocessResponseSchema,
  recordPageSchema,
  reviewPageSchema,
  saveResponseSchema,
  spendSummarySchema,
  stepInfoSchema,
} from "./validation";

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

/**
 * The server answered, but not with the shape this build expects.
 *
 * Distinct from ApiError because the cause is different and so is the fix: a schema mismatch
 * means the UI and the API are out of step (usually a deploy that shipped one and not the other),
 * not that the request was wrong. Failing here is deliberate — rendering `undefined` three
 * components deep is far harder to diagnose than a message naming the field.
 */
export class ApiContractError extends Error {
  constructor(
    public readonly path: string,
    public readonly issues: string,
    public readonly requestId: string | null,
  ) {
    super(`Unexpected response from ${path}: ${issues}`);
  }
}

export const isAbort = (error: unknown) =>
  error instanceof DOMException && error.name === "AbortError";

async function request<T>(
  path: string,
  schema: z.ZodType<T>,
  init: RequestInit = {},
): Promise<T> {
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
  const parsed = schema.safeParse(await response.json());
  if (!parsed.success) {
    const issues = parsed.error.issues
      .slice(0, 3)
      .map((issue) => `${issue.path.join(".") || "(root)"}: ${issue.message}`)
      .join("; ");
    throw new ApiContractError(path, issues, requestId);
  }
  return parsed.data;
}

const json = (body: unknown, signal?: AbortSignal): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
  signal,
});

export const api = {
  health: (signal?: AbortSignal) => request("/health", healthSchema, { signal }),
  steps: (signal?: AbortSignal) => request("/steps", z.array(stepInfoSchema), { signal }),

  load: (source: "x" | "huggingface", limit: number, query: string, signal?: AbortSignal) =>
    request("/dataset/load", datasetSummarySchema, json({ source, limit, query: query || null }, signal)),

  upload: (file: File, signal?: AbortSignal) => {
    const form = new FormData();
    form.append("file", file);
    return request("/dataset/upload", datasetSummarySchema, { method: "POST", body: form, signal });
  },

  preprocess: (
    datasetId: string,
    steps: string[],
    options: StepOptions,
    explain: boolean,
    signal?: AbortSignal,
  ) =>
    request(
      `/dataset/${datasetId}/preprocess`,
      preprocessResponseSchema,
      json({ steps, options, explain }, signal),
    ),

  /** All records in one call; the grid holds them in memory and virtualises rows itself. */
  allRecords: async (datasetId: string, total: number, signal?: AbortSignal) => {
    const PAGE = 1000; // server caps a page at 1000
    const items: RecordPage["items"] = [];
    for (let offset = 0; offset < total; offset += PAGE) {
      const page = await request(
        `/dataset/${datasetId}/records?offset=${offset}&limit=${PAGE}`,
        recordPageSchema,
        { signal },
      );
      items.push(...page.items);
      if (page.items.length < PAGE) break;
    }
    return items;
  },

  labelSummary: (datasetId: string, signal?: AbortSignal) =>
    request(`/dataset/${datasetId}/labels/summary`, labelSummarySchema, { signal }),

  labelEstimate: (datasetId: string, signal?: AbortSignal) =>
    request(`/dataset/${datasetId}/labels/estimate`, labelEstimateSchema, { signal }),

  /** One slice of Comprehend labelling; the caller loops until `done`. */
  labelComprehend: (datasetId: string, maxRecords: number, signal?: AbortSignal) =>
    request(
      `/dataset/${datasetId}/labels/comprehend`,
      labelProgressSchema,
      json({ max_records: maxRecords, confirm_cost: true }, signal),
    ),

  chooseReview: (datasetId: string, mode: ReviewMode, size: number, unit: SampleUnit, signal?: AbortSignal) =>
    request(`/dataset/${datasetId}/labels/review`, labelSummarySchema, {
      ...json({ mode, size, unit, seed: 7 }, signal),
      method: "PUT",
    }),

  reviewPage: (datasetId: string, offset: number, limit: number, signal?: AbortSignal) =>
    request(`/dataset/${datasetId}/labels/review?offset=${offset}&limit=${limit}`, reviewPageSchema, { signal }),

  manualLabels: (datasetId: string, items: { id: string; label: SentimentLabel }[], signal?: AbortSignal) =>
    request(`/dataset/${datasetId}/labels/manual`, labelSummarySchema, json({ items }, signal)),

  checkpoints: (datasetId: string, signal?: AbortSignal) =>
    request(`/dataset/${datasetId}/checkpoints`, checkpointListSchema, { signal }),

  convertCheckpoint: (datasetId: string, stage: CheckpointStage, signal?: AbortSignal) =>
    request(`/dataset/${datasetId}/checkpoints/${stage}/parquet`, checkpointInfoSchema, { method: "POST", signal }),

  save: (datasetId: string, signal?: AbortSignal) =>
    request(`/dataset/${datasetId}/save`, saveResponseSchema, { method: "POST", signal }),

  spend: (includeXUsage = false, signal?: AbortSignal) =>
    request(`/spend?include_x_usage=${includeXUsage}`, spendSummarySchema, { signal }),

  history: (signal?: AbortSignal) => request("/history?limit=25", z.array(historyRunSchema), { signal }),

  exportUrl: (datasetId: string, kind: "csv" | "xlsx" | "parquet" | "md") =>
    kind === "md"
      ? `${BASE}/dataset/${datasetId}/report.md`
      : `${BASE}/dataset/${datasetId}/export.${kind}`,

};
