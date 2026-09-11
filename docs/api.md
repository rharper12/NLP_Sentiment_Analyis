# API reference

Swagger UI: `/docs`. OpenAPI JSON: `/openapi.json`. This page is the narrative; the schema is the
contract.

Every response includes `X-Request-Id`. Error bodies are uniform:

```json
{"error": "unknown step 'bogus'; valid steps: [...]", "request_id": "…"}
```

| Status | Meaning |
|---|---|
| 400 | Request understood but wrong: bad CSV, unknown step, empty X query |
| 404 | Dataset id not found (working state expires after 7 days in Lambda) |
| 422 | Body failed schema validation (FastAPI default) |
| 503 | A required setting is missing, e.g. no X token, no data bucket for save |
| 500 | Bug. Quote the request id. |

## Dataset

**POST `/dataset/load`** `{ "source": "x" | "huggingface", "limit": 600, "query": "…" }`
Fetches up to `limit` records. `query` is required for X and accepts any recent-search operators;
`lang:en -is:retweet` is appended unless `lang:` is present. If the client disconnects, the X
fetch stops at the next page. Response: `DatasetSummary` with `dataset_id`, `record_count`,
`truncated_reason`, `estimated_cost_usd` (X only), `warnings`, and a 20-record `preview`.

**POST `/dataset/upload`** multipart `file` (CSV with `text`, optional `label`/`id`).

**GET `/dataset/{id}`** summary and preview.

**GET `/dataset/{id}/records?offset=0&limit=100&search=…`** pages of `{original, processed}` pairs;
`processed` is null when a step dropped the record.

## Preprocess

**GET `/steps`** catalogue in recommended order with `title`, `summary`, `strengths`, `limitations`.

**POST `/dataset/{id}/preprocess`**
```json
{ "steps": ["missing_data", "lowercase", "punctuation", "tokenize", "stopwords", "lemmatize"],
  "options": { "missing_data_strategy": "drop", "keep_negations": true },
  "explain": true }
```
Always re-runs from the original data. Returns `metrics_before`, `metrics_after`, `report`
(`ImpactReport`: per-step `StepResult`s, optional `sentiment`, `embedding_drift`, `explanation`,
and `warnings` explaining any null), and a processed `preview`.

## Label

**GET `/dataset/{id}/labels/summary`** counts by label and by source (`source`, `comprehend`,
`manual`), review progress, and reviewer-vs-Comprehend agreement.

**GET `/dataset/{id}/labels/estimate`** billable units to label every record Comprehend has not
seen yet, and dollars when a current rate is known. The rate comes from the AWS Price List Query
API (`pricing:GetProducts`, ServiceCode `AmazonComprehend`, filtered by `regionCode`), cached 24 h
in the history database. `price_status` is `live`, `cached`, `stale` (lookup failed, older cache
served) or `unavailable` (no rate at all: `estimated_cost_usd` is null and the UI tells the person
to price the job manually). There is deliberately no hard-coded fallback price.

**POST `/dataset/{id}/labels/comprehend`** `{ "max_records": 250, "confirm_cost": true }`
Labels one slice; call again until `done`. Records that already have a Comprehend label are
skipped, so a crash or cancel never re-bills. The bundle is saved and the `labelled` checkpoint
rewritten after every call. Refused (400) without `confirm_cost`; 503 if Comprehend is disabled.

**PUT `/dataset/{id}/labels/review`** `{ "mode": "none" | "all" | "sample", "size": 150, "unit": "count" | "percent", "seed": 7 }`
Fixes the review set (stable order for a seed).

**GET `/dataset/{id}/labels/review?offset&limit`** the review set, in order.

**POST `/dataset/{id}/labels/manual`** `{ "items": [ { "id": "…", "label": "positive" } ] }`
Sets `label_source="manual"`; Comprehend's label is kept alongside for comparison.

## Checkpoints

**GET `/dataset/{id}/checkpoints`** the `collected`, `processed` and `labelled` snapshots and
their location (`local` folder or `s3`).

**POST `/dataset/{id}/checkpoints/{stage}/parquet`** converts that stage's CSV snapshot to Parquet
next to it.

## Export

| Endpoint | Output |
|---|---|
| GET `/dataset/{id}/export.csv` | UTF-8 CSV with BOM: id, source_type, label, label_source, label_confidence, comprehend_label, comprehend_confidence, created_at, original_text, processed_text, tokens |
| GET `/dataset/{id}/export.parquet` | Same rows, typed and compressed; the file Task 2 should load |
| GET `/dataset/{id}/export.xlsx` | Sheet `data` as above; sheet `impact` with per-step statistics |
| GET `/dataset/{id}/report.md` | Task 1 Markdown report, including a Labels section |
| POST `/dataset/{id}/save` | `dataset.parquet`, `impact.json`, `manifest.json` under `s3://{bucket}/datasets/{id}/{timestamp}/`; returns `{ "uri": … }` |

## Account

**GET `/spend?include_x_usage=false`** today/month reads and cost from the local ledger, the caps,
`remaining_today`, and (when asked and available) X's own `x_usage` object.

**GET `/history?limit=20`** recent pipeline runs with headline metrics.

## System

**GET `/health`** `status`, `version`, `diagnostics`, `x_configured`. When `diagnostics` is true
(local runtime by default, `DIAGNOSTICS` overrides) it also returns `runtime`, `database`,
`database_ephemeral`, `checkpoints`, `comprehend_enabled`, `bedrock_enabled`; otherwise those are
null so a public deployment reveals nothing about its internals. Never touches paid services.

