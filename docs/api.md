# API reference

Swagger UI: `/docs`. OpenAPI JSON: `/openapi.json`. This page is the narrative; the schema is the
contract.

**GET `/dataset/{id}/original.json`** downloads a portable `sentiment-prep-original-v1`
snapshot containing `original` dataset records and collection provenance. It excludes processed
text, later manual/Comprehend annotations, and application/billing state; original source labels
are retained. This download is available immediately after collection.

**GET `/local-datasets?offset=0&limit=50`** lists local working JSON files by modification time,
newest first (filename breaks ties). Returns `total` and `items` containing `dataset_id`,
`filename`, `modified_at`, and `bytes`. Maximum page size is 100. Reads only file metadata;
symlinks and non-regular files are excluded. Requires `RUNTIME=local`; deployed requests return
404 even with diagnostics enabled. `/health.local_datasets_available` controls the picker.

**POST `/local-datasets/{id}/restore`** validates the selected working JSON from
`CHECKPOINT_DIR/_work` and creates a new dataset from its original rows. Requires the local
runtime. Arbitrary paths and symbolic links are rejected. Files must contain 1–5,000 original
records. Preserves IDs, exact text, dates, query, and source labels, without deduplicating again.
Later labels, processing, review, analysis, and billing state are reset. The original file stays
intact. The UI opens Clean with unchecked options. Browser uploads to `/dataset/restore` have
been removed; original JSON downloads remain available as archives.

Consumer collections reopen the existing working bundle instead of cloning original-only rows,
preserving decisions, exclusions, policy, cursors and accounting. Empty partial collections are
allowed; the 5,000 target bound allows up to nine retained page-minimum extras per requested day.
No legacy record is inferred to be eligibility-reviewed or sentiment-reviewed.

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

For general X searches, `limit` targets retained posts after language/content filtering and configured exact and
near-duplicate removal. Collection continues from the next cursor to replace dropped posts;
all provider reads still count toward the existing spending caps. A short collection with a
cursor remains resumable when paused. Request-time pauses report `request budget reached`,
while provider HTTP 429 pauses report `rate limited` and a `retry_at` time. Older collections
marked complete before final duplicate removal can resume with the same `request_id` and
request parameters when they remain below the target and have an unused cursor.

**POST `/dataset/load`** `{ "source": "x" | "huggingface", "limit": 600, "query": "…" }`
Fetches up to `limit` records. `query` is required for X and accepts X search operators;
`lang:en -is:retweet` is appended unless `lang:` is present. If the client disconnects, the X
fetch stops at the next page. Response: `DatasetSummary` with `dataset_id`, `record_count`,
`truncated_reason`, `billed_reads` and `committed_cost_usd` (X only), `warnings`, and a 20-record `preview`.

Optional `start_time` and `end_time` are timezone-aware ISO timestamps. Dates older than seven
days automatically use `/2/tweets/search/all`, with the existing bearer token and pay-per-use
or Enterprise archive access. Historical start dates are preserved; near-present end times
are moved back 30 seconds. The earliest supported date is March 1, 2006. Without dates, search
covers the last seven days. An end-only historical request searches the seven days ending at
that timestamp. Both endpoints share the same read caps and cost accounting. Saved pagination
keeps its original endpoint and effective window; an expired recent-search job requires a new
request ID instead of transferring its cursor to the archive.

For example, send `"start_time": "2026-09-09T00:00:00Z"` to collect from September 9 through now.

Alternatively supply `start_date`, `end_date` (final included date), and an IANA `timezone`.
These produce inclusive start/exclusive next-midnight UTC boundaries, including DST transitions.
Both dates are required; reversed, unfinished, unsupported or contradictory calendar/timestamp
input is rejected before collection. General calendar searches retain longer archive ranges.

### Consumer collections

`POST /dataset/load` accepts the separate preset:

```json
{"source":"x","request_id":"new-consumer-pilot","preset":"consumer_reactions",
 "query":"\"coffee maker\"",
 "start_date":"2026-09-09","end_date":"2026-09-10","timezone":"America/Chicago",
 "limit":100,"per_author_limit":2,"reviewed_target":500}
```

`query` is required and cannot be blank. The preset supplies no product, dates, or announcement
assumptions. This example's window is `[2026-09-09T05:00:00Z, 2026-09-11T05:00:00Z)`.
New collections use `consumer-reactions-v2`: content screening is topic-neutral and the reviewer
confirms relevance to the saved query. Existing `consumer-reactions-v1` collections retain their
original product-specific rules and saved reviews on resume; they are not migrated implicitly.
This preset supports 1–31 completed days, with at least ten candidates per day. `limit` is a
candidate target, split across independent daily cursors. Short/ambiguous/non-English/promotional
candidates remain recoverable for content review; timestamp validation still rejects out-of-window
rows. Duplicate matches annotate candidates rather than deleting them. All returned reads count
toward conservative estimates and existing caps, including local rejections and repeated IDs.

`DatasetSummary.consumer_policy` records the frozen policy. `consumer_counts` distinguishes
`retrieved` (successful returned rows), `unique_records` (unique returned IDs, including date
rejections), `screened_candidates`, `pending_eligibility`, `human_inclusions`, `human_exclusions`,
`included` (after author cap), `author_cap_held`, `missing_author`, `pending_sentiment`,
`reviewed_final`, `reviewed_target`, `shortfall`, and per-day candidate/included/final counts.
Legacy `record_count`, `labelled_count`, and billing counters retain their meanings. Collection
`partial` means resumable provider work; it does not imply reviewed-target completion.
`first_batch_saved` and `last_batch_saved` count saved rows added by the first and latest
collection requests, respectively. They are persisted with each page, survive read-only reloads,
and exclude repeated IDs and rejected source rows. They are not provider reads or reviewed
counts. Missing historical batch counts remain `null`; a request adding no rows reports zero.

**POST `/dataset/{id}/candidates`** `{"candidate_target":200,"confirm_cost":true}` explicitly
extends the absolute candidate target or resumes the same target. Repeating a target is idempotent;
decreasing it is rejected. It reuses saved criteria, seen IDs, day cursors and cumulative job caps.
Once `consumer_counts.shortfall` is zero, additional collection returns 400 without modifying
the quota or contacting X. A later review correction that creates a shortfall permits it again.
Collect's **Get more posts** resumes only the authorized collection goal. Review can extend it;
the suggested quota subtracts pending reviews from the shortfall and respects remaining capacity.
Each explicit request stays on the current step. Paused batches resume the same absolute target.
No request follows automatically from an exclusion. Query/date/policy changes require a new context.
`candidate_target` and `can_collect_more` describe collection capacity, not training completion.
Ordinary budget pauses have no `retry_at`; genuine throttles do. Terminal provider 4xx errors
other than 429 preserve progress but cannot be resumed indefinitely. Archive refusal never
falls back to recent results. See the [pilot and policy guide](how-the-app-works.md#consumer-reactions-and-a-small-pilot).

**GET `/dataset/{id}/eligibility?offset=0&limit=100&status=all`** returns original records,
`total`, `offset`, current `included_ids` and counts. Statuses: `all`, `pending`, `include`,
`exclude`, `sentiment`, `needs_review`. The `sentiment` view selects current author-capped inclusions, including completed
sentiment decisions for correction. Include/exclude queues mean human decisions, not suggestions.
With `status=all`, optional `start_at=first_unreviewed` seeks the first unresolved eligibility
choice; `start_at=first_unlabeled` also includes kept posts awaiting manual sentiment. Both
return an offset into the original ordering and the total candidate count. If finished, offset
equals total and items is empty. Navigate subsequent pages by offset so saved and excluded posts
remain revisitable. These read-only requests do not approve posts or call sentiment services.

**PUT `/dataset/{id}/eligibility`**
`{"items":[{"id":"123","decision":"include","label":"positive"}]}`
persists explicit confirmations. Decisions are `include`, `exclude`, `pending`. Reasons and notes
are optional, including for screening overrides. Existing reasons, notes and human history
remain readable;
pending undoes review, and changing eligibility requires sentiment reconfirmation. Labels and
machine scores remain recoverable. Both unchanged confirmations persist as completed review.
The eligibility PUT also accepts an optional `label` (positive/negative/neutral/mixed) with an
`include` decision. It saves eligibility and human sentiment under one exclusive edit, so a failed
validation cannot leave half a review. Labels on exclusions or pending decisions are rejected.
`GET .../eligibility?status=needs_review` returns unresolved eligibility and selected inclusions
without reviewed sentiment; completed posts leave that queue. Existing eligibility-only requests
remain supported. Duplicate record IDs in a review request are rejected.

Author-limit holds retain combined manual labels, while selection remains independent of sentiment.
Only current author-selected inclusions may use the separate manual/Comprehend sentiment routes.
The consumer UI offers review with or without manual sentiment, or skipping review without
changing human-review flags. Preprocessing does not automatically call cloud scoring. A later
explicit Comprehend request preserves manual labels and stores predictions separately; it may
still score manually labeled posts for comparison, so they count in the cost estimate.

**POST `/dataset/upload`** multipart `file` (UTF-8 CSV with `text`, optional `label`/`id`),
limited to 4 MiB and 5,000 data rows. Validates the entire file, even with a smaller requested
limit. Rejects malformed CSV, invalid UTF-8, NUL bytes, missing/duplicate/empty headers,
inconsistent row widths, duplicate IDs, and files with no usable text. Blank text is skipped;
extra columns are ignored and existing label strings are lowercased.

**POST `/dataset/upload/validate`** accepts the same multipart `file` and applies the same parser
without creating datasets, checkpoints, or history entries. Returns `record_count` (non-empty
rows), `skipped_empty`, `labelled_count`, and a preview of up to three posts. The browser validates
on selection/drop, then enables Import CSV; the import endpoint revalidates independently.
Both routes bound streamed multipart bodies before parsing. Invalid content returns 400 and
oversized files return 413. Row counts precede optional collection deduplication.

**GET `/dataset/{id}`** summary and preview.

**GET `/dataset/{id}/records?offset=0&limit=100&search=…`** pages of `{original, processed}` pairs;
`processed` is null when a step dropped the record.

## Preprocess

**GET `/steps`** catalogue in recommended order with `group`, `title`, `summary`, `strengths`, `limitations`.
Groups are cleaning, normalization, and the final empty-record sweep.

**POST `/dataset/{id}/preprocess`**
```json
{ "steps": ["lowercase", "punctuation", "tokenize", "stopwords", "lemmatize", "missing_data"],
  "options": { "missing_data_strategy": "drop", "keep_negations": true } }
```
Always re-runs from the original data. Returns `metrics_before`, `metrics_after`, `report`
(`ImpactReport`: per-step `StepResult`s, optional `sentiment`,
and analysis `warnings`), public-safe persistence `warnings`, and a processed `preview`.
Large jobs may return `partial=true`; repeating the same request resumes saved analysis units.
An empty `steps` list analyzes the original text without preprocessing. The Clean screen starts
with every checkbox unchecked. No Bedrock calls or generated explanations are supported.

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

**PUT `/dataset/{id}/labels/review`** `{ "mode": "none" | "all" | "sample" | "low_confidence", "size": 150, "unit": "count" | "percent", "seed": 7 }`
Fixes the review set. Random samples have stable order for a seed. `low_confidence` excludes
already manually reviewed records, places unscored records first, then sorts by ascending
Comprehend confidence (record ID breaks ties). Percent sizes use the eligible unreviewed count.
This selection finds likely errors; it is not a representative accuracy sample.

**GET `/dataset/{id}/labels/review?offset&limit`** the review set, in order, plus `reviewed`: the saved manual-label count across the whole selection.

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
| GET `/dataset/{id}/reviewed.parquet` | Consumer-only training rows: included, eligibility-reviewed, valid sentiment, sentiment-reviewed, after author cap; policy/query/counts/status embedded under Parquet schema metadata `sentiment_prep` |
| GET `/dataset/{id}/export.xlsx` | Sheet `data` as above; sheet `impact` with per-step statistics |
| GET `/dataset/{id}/report.md` | Task 1 Markdown report, including a Labels section |
| POST `/dataset/{id}/save` | `{filename}.parquet`, `impact.json`, `manifest.json` under `s3://{bucket}/datasets/{id}/{filename}/{save_id}/`; returns `{ "uri": … }` |

For consumer collections, use `reviewed.parquet` for training examples. Other modes remain full
candidate archives including exclusions and unreviewed rows. Reviewed output preserves automated
labels/confidence alongside human labels and decision history; optional confidence may be null.
Its default filename ends in `-reviewed.parquet`. Embedded `status` is `partial` until the reviewed
target is reached. Every request regenerates current decisions and downloads send `no-store`.
Eligibility, source or label changes clear affected analysis/processing and stale downstream
snapshots; rejected checkpoint conversions require regeneration. No additional companion files.
These routes share the existing router authentication and dataset edit/storage protections.

Exports and S3 saves accept an optional `filename` query parameter **without an extension**.
Use 1–120 ASCII letters, numbers, spaces, hyphens or underscores, starting with a letter or
number. Invalid names return 422 before generating data or accessing S3. Each endpoint owns
its extension. Renaming an export does not rename the working dataset or change its contents.

Dataset summaries expose `file_stem`, the default name shared by downloads and S3 saves.
On collection/import/restore the browser sends its IANA timezone in `X-Time-Zone`; clients
that omit it use UTC. The name combines a sanitized topic with creation time and UTC offset,
for example `iphone-duo-2026-09-23_12-15-30-UTC-0500`. CSV and sample imports use `csv-import`
and `sample-tweets` when no query exists. Names stay stable across processing and X retries.
New local bundles live at `CHECKPOINT_DIR/_work/{dataset_id}/{file_stem}.json`; legacy flat
`{dataset_id}.json` files remain readable. Dataset IDs isolate same-second name collisions.
S3 saves use independent save IDs so repeated saves do not overwrite previous outputs.

## Account

**GET `/spend?include_x_usage=false`** today/month reads and cost from the local ledger, the caps,
`remaining_today`, and (when asked and available) X's own `x_usage` object.

**GET `/history?limit=20`** recent pipeline runs with headline metrics.

## System

**GET `/health`** `status`, `version`, `diagnostics`, `x_configured`, `auth_required`, and
`x_cost_per_read_usd` for preflight estimates. When `diagnostics` is true
(local runtime by default, `DIAGNOSTICS` overrides) it also returns `runtime`, `database`,
`database_ephemeral`, `checkpoints`, `comprehend_enabled`; otherwise those are
omitted; configured authentication is required before operator details are returned. Never touches paid services.
