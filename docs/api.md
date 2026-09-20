# API reference

Swagger UI: `/docs`. OpenAPI JSON: `/openapi.json`. This page is the narrative; the schema is the
contract.

When `API_KEY` is configured, browser users exchange it at `POST /auth/session` for a one-hour
in-memory bearer token. Protected endpoints accept `Authorization: Bearer <token>` or an
operator script’s `X-API-Key`. Liveness and the session-exchange endpoint are public. Every response includes `X-Request-Id`. Error bodies are uniform:

```json
{"error": "unknown step 'bogus'; valid steps: [...]", "request_id": "…"}
```

| Status | Meaning |
|---|---|
| 400 | Request understood but wrong: bad CSV, unknown step, empty X query |
| 404 | Dataset id not found (working state expires after 7 days in Lambda) |
| 422 | Body failed schema validation (FastAPI default) |
| 401 | Missing, expired, or invalid authentication (only when a key is configured) |
| 502 | An upstream API refused or failed; the message says which and what to check |
| 503 | A required setting is missing, e.g. no X token, no data bucket for save |
| 500 | Bug. Quote the request id. |

## Dataset

X requests use a stable `request_id`; resuming must reuse it with the same query/limit/window.
Partial responses retain committed pages and expose `partial`, `resume_request_id`, and
`retry_at`. Retained records and billed reads can differ after filtering. Committed cost uses
the rate at settlement, rather than repricing earlier reads after a configuration change.
Older X datasets without stored billing totals return null; the API does not reconstruct
historical spend using today's configured rate.

**POST `/dataset/load`** `{ "source": "x" | "huggingface", "limit": 600, "query": "…" }`
Fetches up to `limit` records. `query` is required for X and accepts any recent-search operators;
`lang:en -is:retweet` is appended unless `lang:` is present. If the client disconnects, the X
fetch stops at the next page. Response: `DatasetSummary` with `dataset_id`, `record_count`,
`truncated_reason`, `billed_reads` and `committed_cost_usd` (X only), `warnings`, and a 20-record `preview`.

**POST `/dataset/upload`** multipart `file` (UTF-8 CSV with `text`, optional `label`/`id`),
limited to 4 MiB and 5,000 rows.

**GET `/dataset/{id}`** summary and preview.

**GET `/dataset/{id}/records?offset=0&limit=100&search=…`** pages of `{original, processed}` pairs;
`processed` is null when a step dropped the record.

## Preprocess

**GET `/steps`** catalogue in recommended order with `group`, `title`, `summary`, `strengths`, `limitations`.
Groups are cleaning, normalization, and the final empty-record sweep.

**POST `/dataset/{id}/preprocess`**
```json
{ "steps": ["lowercase", "punctuation", "tokenize", "stopwords", "lemmatize", "missing_data"],
  "options": { "missing_data_strategy": "drop", "keep_negations": true },
  "explain": true }
```
Always re-runs from the original data. Returns `metrics_before`, `metrics_after`, `report`
(`ImpactReport`: per-step `StepResult`s, optional `sentiment`, `embedding_drift`, `explanation`,
and analysis `warnings`), public-safe persistence `warnings`, and a processed `preview`.
Large jobs may return `partial=true`; repeating the same request resumes saved analysis units.

## Label

**GET `/dataset/{id}/labels/summary`** counts by label and by source (`source`, `comprehend`,
`manual`), current review-set progress, and agreement across all manual/machine pairs.
`manually_reviewed`, `machine_scored`, `comparable_records`, and `agreements` define the
cohort explicitly. Agreement is null when no comparable records exist.

**GET `/dataset/{id}/labels/estimate`** billable units to label every record Comprehend has not
seen yet, and dollars when a current rate is known. The rate comes from the AWS Price List Query
API (`pricing:GetProducts`, ServiceCode `AmazonComprehend`, filtered by `regionCode`), cached 24 h
in the history database. `price_status` is `live`, `cached`, `stale` (lookup failed, within the additional 48 h grace) or `unavailable` (missing, invalid, future-dated, or expired quote: `estimated_cost_usd` is null and the UI tells the person
to price the job manually). There is deliberately no hard-coded fallback price.

**POST `/dataset/{id}/labels/comprehend`** `{ "max_records": 250, "confirm_cost": true }`
Labels one slice; call again until `done`. Records that already have a Comprehend label are
skipped. Provider success immediately before persistence remains ambiguous; no exactly-once
billing guarantee is made. The bundle is saved and the `labelled` checkpoint
rewritten after every call. Refused (400) without `confirm_cost`; 503 if Comprehend is disabled.

**PUT `/dataset/{id}/labels/review`** `{ "mode": "none" | "all" | "sample", "size": 150, "unit": "count" | "percent", "seed": 7 }`
Fixes the review set (stable order for a seed).

**GET `/dataset/{id}/labels/review?offset&limit`** the review set, in order.

**POST `/dataset/{id}/labels/manual`** `{ "items": [ { "id": "…", "label": "positive" } ] }`
Sets `label_source="manual"`; Comprehend's label is kept alongside for comparison.

## Checkpoints

**GET `/dataset/{id}/checkpoints`** the `collected`, `processed` and `labelled` snapshots and
their location (`local` folder or `s3`), content revision, and `current`/`stale`/`failed`
status. This endpoint and conversion require diagnostics authorization. Failed replacements
retain prior snapshots and surface public-safe warnings.

**POST `/dataset/{id}/checkpoints/{stage}/parquet`** converts that stage's CSV snapshot to Parquet
next to it. Outdated CSVs must be regenerated first; replacing a CSV makes older Parquet
conversions stale. Rerunning preprocessing also marks existing labelled snapshots stale.

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

**GET `/health`** `status`, `version`, `diagnostics`, `x_configured`, `auth_required`, and
`x_cost_per_read_usd` for preflight estimates. When `diagnostics` is true
(local runtime by default, `DIAGNOSTICS` overrides) it also returns `runtime`, `database`,
`database_ephemeral`, `checkpoints`, `comprehend_enabled`, `bedrock_enabled`; otherwise those are
omitted; configured authentication is required before operator details are returned. Never touches paid services.

