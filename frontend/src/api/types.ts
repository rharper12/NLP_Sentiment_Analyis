// Single source of truth for API shapes: every type below is derived from the backend's OpenAPI
// schema (`npm run gen:api` regenerates `schema.d.ts`), so a Pydantic change that this code has
// not accounted for becomes a type error rather than a runtime surprise. Nothing here is
// hand-copied. Only UI-local unions and constants are declared by hand.

import type { components } from "./schema";

type S = components["schemas"];

/** One collected post. Named PostRecord so it cannot shadow TypeScript's built-in Record<K, V>. */
export type PostRecord = S["Record"];
export type CsvValidation = S["CsvValidation"];
export type LocalDatasetPage = S["LocalDatasetPage"];
export type DatasetSummary = S["DatasetSummary"];
export type StepInfo = S["StepInfo"];
export type StepResult = S["PublicStepResult"];
export type SentimentComparison = S["SentimentComparison"];
export type ImpactReport = S["PublicImpactReport"];
export type DatasetMetrics = S["DatasetMetrics"];
export type PreprocessResponse = S["PreprocessResponse"];
export type RecordPair = S["RecordPair"];
export type RecordPage = S["RecordPage"];
export type StepOptions = S["StepOptions"];
export type SpendSummary = S["SpendSummary"];
export type HistoryRun = S["HistoryRun"];
export type HealthResponse = S["HealthResponse"];
export type LabelEstimate = S["LabelEstimate"];
export type LabelProgress = S["LabelProgress"];
export type LabelSummary = S["LabelSummary"];
export type ReviewPage = S["ReviewPage"];
export type CheckpointInfo = S["CheckpointInfo"];
export type CheckpointList = S["CheckpointList"];
export type ConsumerCounts = S["ConsumerCounts"];
export type ConsumerPolicy = S["ConsumerPolicy"];
export type EligibilityPage = S["EligibilityPage"];
// Requests may omit the note; the API supplies its empty default.
export type EligibilityItem = Omit<S["EligibilityItem"], "note"> &
  Partial<Pick<S["EligibilityItem"], "note">>;
export type CollectionWindow = Partial<Pick<S["LoadRequest"], "start_date" | "end_date" | "timezone" | "preset" | "per_author_limit" | "reviewed_target">> & { start?: string; end?: string };

export type LabelSource = NonNullable<PostRecord["label_source"]>;
export type SentimentLabel = NonNullable<S["ManualLabelItem"]["label"]>;
export type ReviewMode = S["ReviewRequest"]["mode"];
export type SampleUnit = NonNullable<S["ReviewRequest"]["unit"]>;
export type CheckpointStage = CheckpointInfo["stage"];

/** Order the reviewer presents them in, and the order of the 1-4 keyboard shortcuts. */
export const SENTIMENT_LABELS: SentimentLabel[] = ["positive", "negative", "neutral", "mixed"];
