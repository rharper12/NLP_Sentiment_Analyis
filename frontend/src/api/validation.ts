/**
 * Runtime validation for everything the API returns.
 *
 * The generated types in `schema.d.ts` describe what the backend *claims* to send; these schemas
 * check what it actually sent. Each one is annotated `z.ZodType<T>` against the generated type,
 * so the two cannot drift: regenerate the types after a Pydantic change and any schema that no
 * longer matches fails to compile.
 *
 * Objects are *loose* (unknown keys pass through) on purpose. A deployed UI is often older than
 * the API in front of it, and a field added server-side must not break a page that never reads
 * it. Missing or wrong-typed fields still fail, which is the case that actually causes bugs.
 */

import { z } from "zod";

import type {
  CheckpointInfo,
  CheckpointList,
  DatasetMetrics,
  DatasetSummary,
  HealthResponse,
  HistoryRun,
  ImpactReport,
  LabelEstimate,
  LabelProgress,
  LabelSummary,
  PostRecord,
  PreprocessResponse,
  RecordPage,
  RecordPair,
  ReviewPage,
  SentimentComparison,
  SpendSummary,
  StepInfo,
  StepResult,
} from "./types";

/** Nullable-and-optional, the shape FastAPI emits for a field with a `None` default. */
const maybe = <T extends z.ZodTypeAny>(inner: T) => inner.nullish();

const counts = z.record(z.string(), z.number());

export const recordSchema: z.ZodType<PostRecord> = z.looseObject({
  id: z.string(),
  text: z.string(),
  source_type: z.enum(["x", "huggingface", "csv"]),
  label: maybe(z.string()),
  label_source: maybe(z.enum(["source", "comprehend", "manual"])),
  label_confidence: maybe(z.number()),
  comprehend_label: maybe(z.string()),
  comprehend_confidence: maybe(z.number()),
  created_at: maybe(z.string()),
  tokens: maybe(z.array(z.string())),
});

export const datasetSummarySchema: z.ZodType<DatasetSummary> = z.looseObject({
  dataset_id: z.string(),
  source_type: z.enum(["x", "huggingface", "csv"]),
  query: z.string().nullable(),
  record_count: z.number(),
  labelled_count: z.number(),
  truncated_reason: z.string().nullable(),
  estimated_cost_usd: maybe(z.number()),
  warnings: z.array(z.string()).optional(),
  preview: z.array(recordSchema),
});

export const stepInfoSchema: z.ZodType<StepInfo> = z.looseObject({
  name: z.string(),
  title: z.string(),
  summary: z.string(),
  strengths: z.string(),
  limitations: z.string(),
});

const stepResultSchema: z.ZodType<StepResult> = z.looseObject({
  step_name: z.string(),
  records_in: z.number(),
  records_out: z.number(),
  vocab_before: z.number(),
  vocab_after: z.number(),
  avg_tokens_before: z.number(),
  avg_tokens_after: z.number(),
  duration_ms: z.number(),
  sample_diffs: z.array(z.tuple([z.string(), z.string()])).optional(),
});

const sentimentComparisonSchema: z.ZodType<SentimentComparison> = z.looseObject({
  agreement: z.number(),
  distribution_before: counts,
  distribution_after: counts,
});

const impactReportSchema: z.ZodType<ImpactReport> = z.looseObject({
  steps: z.array(stepResultSchema),
  sentiment: maybe(sentimentComparisonSchema),
  embedding_drift: maybe(z.number()),
  explanation: maybe(z.string()),
  warnings: z.array(z.string()).optional(),
});

const metricsSchema: z.ZodType<DatasetMetrics> = z.looseObject({
  record_count: z.number(),
  vocab_size: z.number(),
  total_tokens: z.number(),
  avg_tokens: z.number(),
  type_token_ratio: z.number(),
  length_histogram: counts,
  top_terms: z.array(z.tuple([z.string(), z.number()])),
});

export const preprocessResponseSchema: z.ZodType<PreprocessResponse> = z.looseObject({
  dataset_id: z.string(),
  applied_steps: z.array(z.string()),
  record_count: z.number(),
  metrics_before: metricsSchema,
  metrics_after: metricsSchema,
  report: impactReportSchema,
  preview: z.array(recordSchema),
});

const recordPairSchema: z.ZodType<RecordPair> = z.looseObject({
  original: recordSchema,
  processed: z.union([recordSchema, z.null()]),
});

export const recordPageSchema: z.ZodType<RecordPage> = z.looseObject({
  total: z.number(),
  offset: z.number(),
  items: z.array(recordPairSchema),
});

export const labelEstimateSchema: z.ZodType<LabelEstimate> = z.looseObject({
  records_total: z.number(),
  records_unlabelled: z.number(),
  records_to_send: z.number(),
  billable_units: z.number(),
  unit_chars: z.number(),
  min_units_per_document: z.number(),
  estimated_cost_usd: z.union([z.number(), z.null()]),
  cost_per_unit_usd: z.union([z.number(), z.null()]),
  price_status: z.enum(["live", "cached", "stale", "unavailable"]),
  price_fetched_at: z.union([z.string(), z.null()]),
  price_region: z.string(),
});

export const labelProgressSchema: z.ZodType<LabelProgress> = z.looseObject({
  labelled_in_call: z.number(),
  labelled_total: z.number(),
  remaining: z.number(),
  units_billed: z.number(),
  cost_usd: z.union([z.number(), z.null()]),
  done: z.boolean(),
});

export const labelSummarySchema: z.ZodType<LabelSummary> = z.looseObject({
  total: z.number(),
  labelled: z.number(),
  by_source: counts,
  by_label: counts,
  review_sample_size: z.number(),
  reviewed: z.number(),
  manual_vs_comprehend_agreement: z.union([z.number(), z.null()]),
  disagreements: z.number(),
});

export const reviewPageSchema: z.ZodType<ReviewPage> = z.looseObject({
  total: z.number(),
  offset: z.number(),
  items: z.array(recordSchema),
});

const checkpointInfoSchema: z.ZodType<CheckpointInfo> = z.looseObject({
  stage: z.enum(["collected", "processed", "labelled"]),
  format: z.enum(["csv", "parquet"]),
  uri: z.string(),
  bytes: z.number(),
  written_at: z.string(),
});

export const checkpointListSchema: z.ZodType<CheckpointList> = z.looseObject({
  location: z.enum(["local", "s3"]),
  items: z.array(checkpointInfoSchema),
});

export { checkpointInfoSchema };

export const spendSummarySchema: z.ZodType<SpendSummary> = z.looseObject({
  today_reads: z.number(),
  today_cost_usd: z.number(),
  month_reads: z.number(),
  month_cost_usd: z.number(),
  cap_per_fetch: z.number(),
  cap_per_day: z.number(),
  remaining_today: z.number(),
  cost_per_read_usd: z.number(),
  x_usage: z.union([z.record(z.string(), z.unknown()), z.null()]).optional(),
  x_configured: z.boolean(),
});

export const historyRunSchema: z.ZodType<HistoryRun> = z.looseObject({
  dataset_id: z.string(),
  source_type: z.string(),
  query: z.string(),
  record_count: z.number(),
  applied_steps: z.array(z.string()),
  records_out: z.number(),
  vocab_before: z.number(),
  vocab_after: z.number(),
  sentiment_agreement: z.union([z.number(), z.null()]),
  embedding_drift: z.union([z.number(), z.null()]),
  duration_ms: z.number(),
  created_at: z.string(),
});

export const healthSchema: z.ZodType<HealthResponse> = z.looseObject({
  status: z.string(),
  version: z.string(),
  diagnostics: z.boolean(),
  x_configured: z.boolean(),
  auth_required: z.boolean(),
  runtime: maybe(z.string()),
  database: maybe(z.string()),
  database_ephemeral: maybe(z.boolean()),
  checkpoints: maybe(z.enum(["local", "s3"])),
  comprehend_enabled: maybe(z.boolean()),
  bedrock_enabled: maybe(z.boolean()),
});

export const saveResponseSchema = z.looseObject({ uri: z.string() });
