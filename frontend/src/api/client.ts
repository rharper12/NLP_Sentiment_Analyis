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
// Browser sessions are temporary and live only in memory. Reloading requires signing in again.
let sessionToken: string | null = null;
export const AUTH_REQUIRED = "sentiment-prep-auth-required";

/** Shown whenever the request never reached the API, which is nearly always "it isn't running". */
const UNREACHABLE =
  "Cannot reach the service. Please try again.";

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

async function authorizedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (sessionToken) headers.set("Authorization", `Bearer ${sessionToken}`);

  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, { ...init, headers });
  } catch (cause) {
    // fetch only rejects when the request never reached a server: the API is down, the origin is
    // wrong, or the network is gone. "Failed to fetch" tells the person nothing they can act on.
    if (isAbort(cause)) throw cause;
    throw new ApiError(UNREACHABLE, 0, null);
  }

  const requestId = response.headers.get("X-Request-Id");
  if (!response.ok) {
    if (response.status === 401 && path !== "/auth/session") {
      sessionToken = null;
      window.dispatchEvent(new Event(AUTH_REQUIRED));
    }
    let message = response.statusText;
    try {
      const body = (await response.json()) as { error?: string; detail?: unknown };
      message = body.error ?? JSON.stringify(body.detail ?? body);
    } catch {
      // A 5xx with no JSON body is almost always the dev proxy reporting that nothing is
      // listening, not the API reporting a bug. Say the useful thing.
      if (response.status >= 500) message = UNREACHABLE;
    }
    throw new ApiError(message, response.status, requestId);
  }
  return response;
}

async function request<T>(path: string, schema: z.ZodType<T>, init: RequestInit = {}): Promise<T> {
  const response = await authorizedFetch(path, init);
  const requestId = response.headers.get("X-Request-Id");
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

  load: (
    source: "x" | "huggingface",
    limit: number,
    query: string,
    window?: { start?: string; end?: string },
    signal?: AbortSignal,
    requestId?: string,
  ) =>
    request(
      "/dataset/load",
      datasetSummarySchema,
      json(
        {
          source,
          request_id: source === "x" ? requestId ?? crypto.randomUUID() : null,
          limit,
          query: query || null,
          start_time: window?.start ?? null,
          end_time: window?.end ?? null,
        },
        signal,
      ),
    ),

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

  download: async (datasetId: string, kind: "csv" | "xlsx" | "parquet" | "md", signal?: AbortSignal) => {
    const filename = kind === "md" ? `${datasetId}-report.md` : `${datasetId}.${kind}`;
    const path = kind === "md" ? `/dataset/${datasetId}/report.md` : `/dataset/${datasetId}/export.${kind}`;
    const response = await authorizedFetch(path, { signal });
    const blob = await response.blob();
    const disposition = response.headers.get("Content-Disposition");
    const supplied = disposition?.match(/filename="([^"\r\n]+)"/i)?.[1];
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    try {
      anchor.href = url;
      anchor.download = supplied?.split(/[\\/]/).pop() || filename;
      document.body.appendChild(anchor);
      anchor.click();
    } finally {
      anchor.remove();
      // Let the browser consume the click before releasing the backing Blob.
      setTimeout(() => URL.revokeObjectURL(url), 0);
    }
  },

  login: async (key: string, signal?: AbortSignal) => {
    const endpoint = new URL(BASE, window.location.href);
    const local = (host: string) => ["localhost", "127.0.0.1", "[::1]"].includes(host);
    if ((endpoint.protocol !== "https:" && !local(endpoint.hostname)) ||
        (window.location.protocol !== "https:" && !local(window.location.hostname))) {
      throw new ApiError("Operator sign-in requires HTTPS.", 0, null);
    }
    const result = await request("/auth/session", z.object({ token: z.string(), expires_in: z.number() }), json({ key }, signal));
    sessionToken = result.token;
  },
};
