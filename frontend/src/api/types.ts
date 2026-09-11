// Mirrors the Pydantic schemas in src/sentiment_prep/api/schemas.py.

export type SourceType = "x" | "huggingface" | "csv";

export type LabelSource = "source" | "comprehend" | "manual";
export type SentimentLabel = "positive" | "negative" | "neutral" | "mixed";
export const SENTIMENT_LABELS: SentimentLabel[] = ["positive", "negative", "neutral", "mixed"];

export interface Record {
  id: string;
  text: string;
  label: string | null;
  label_source: LabelSource | null;
  label_confidence: number | null;
  comprehend_label: string | null;
  comprehend_confidence: number | null;
  source_type: SourceType;
  created_at: string | null;
  tokens: string[] | null;
}

export interface DatasetSummary {
  dataset_id: string;
  source_type: SourceType;
  query: string | null;
  record_count: number;
  labelled_count: number;
  truncated_reason: string | null;
  estimated_cost_usd: number | null;
  warnings: string[];
  preview: Record[];
}

export interface StepInfo {
  name: string;
  title: string;
  summary: string;
  strengths: string;
  limitations: string;
}

export interface StepResult {
  step_name: string;
  records_in: number;
  records_out: number;
  vocab_before: number;
  vocab_after: number;
  avg_tokens_before: number;
  avg_tokens_after: number;
  duration_ms: number;
  sample_diffs: [string, string][];
}

export interface SentimentComparison {
  agreement: number;
  distribution_before: { [label: string]: number };
  distribution_after: { [label: string]: number };
}

export interface ImpactReport {
  steps: StepResult[];
  sentiment: SentimentComparison | null;
  embedding_drift: number | null;
  explanation: string | null;
  warnings: string[];
}

export interface DatasetMetrics {
  record_count: number;
  vocab_size: number;
  total_tokens: number;
  avg_tokens: number;
  type_token_ratio: number;
  length_histogram: { [bucket: string]: number };
  top_terms: [string, number][];
}

export interface PreprocessResponse {
  dataset_id: string;
  applied_steps: string[];
  record_count: number;
  metrics_before: DatasetMetrics;
  metrics_after: DatasetMetrics;
  report: ImpactReport;
  preview: Record[];
}

export interface RecordPair {
  original: Record;
  processed: Record | null;
}

export interface RecordPage {
  total: number;
  offset: number;
  items: RecordPair[];
}

export interface StepOptions {
  missing_data_strategy: "drop" | "fill";
  keep_negations: boolean;
}

export interface SpendSummary {
  today_reads: number;
  today_cost_usd: number;
  month_reads: number;
  month_cost_usd: number;
  cap_per_fetch: number;
  cap_per_day: number;
  remaining_today: number;
  cost_per_read_usd: number;
  x_usage: { [key: string]: unknown } | null;
  x_configured: boolean;
}

export interface HistoryRun {
  dataset_id: string;
  source_type: string;
  query: string;
  record_count: number;
  applied_steps: string[];
  records_out: number;
  vocab_before: number;
  vocab_after: number;
  sentiment_agreement: number | null;
  embedding_drift: number | null;
  duration_ms: number;
  created_at: string;
}

/** Operator-only fields are null unless `diagnostics` is true (local runtime by default). */
export interface HealthResponse {
  status: string;
  version: string;
  diagnostics: boolean;
  x_configured: boolean;
  runtime: string | null;
  database: string | null;
  database_ephemeral: boolean | null;
  checkpoints: "local" | "s3" | null;
  comprehend_enabled: boolean | null;
  bedrock_enabled: boolean | null;
}

export type PriceStatus = "live" | "cached" | "stale" | "unavailable";

export interface LabelEstimate {
  records_total: number;
  records_unlabelled: number;
  records_to_send: number;
  billable_units: number;
  unit_chars: number;
  min_units_per_document: number;
  /** Null when no current rate is known; never a guessed figure. */
  estimated_cost_usd: number | null;
  cost_per_unit_usd: number | null;
  price_status: PriceStatus;
  price_fetched_at: string | null;
  price_region: string;
}

export interface LabelProgress {
  labelled_in_call: number;
  labelled_total: number;
  remaining: number;
  units_billed: number;
  cost_usd: number | null;
  done: boolean;
}

export interface LabelSummary {
  total: number;
  labelled: number;
  by_source: { [source: string]: number };
  by_label: { [label: string]: number };
  review_sample_size: number;
  reviewed: number;
  manual_vs_comprehend_agreement: number | null;
  disagreements: number;
}

export type ReviewMode = "none" | "all" | "sample";
export type SampleUnit = "count" | "percent";

export interface ReviewPage {
  total: number;
  offset: number;
  items: Record[];
}

export type CheckpointStage = "collected" | "processed" | "labelled";

export interface CheckpointInfo {
  stage: CheckpointStage;
  format: "csv" | "parquet";
  uri: string;
  bytes: number;
  written_at: string;
}

export interface CheckpointList {
  location: "local" | "s3";
  items: CheckpointInfo[];
}
