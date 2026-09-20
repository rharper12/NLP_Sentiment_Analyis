# Logging and debugging

The app uses **structlog**, the Python counterpart to Pino: every log line is a dict, context is
bound once per request and merged into every subsequent line, and the output renderer depends on
the environment (JSON in Lambda and CI, coloured key=value in a terminal).

## Reading a line

```json
{"status": 200, "duration_ms": 253.1, "method": "POST", "path": "/dataset/9f2c1a/preprocess",
 "request_id": "b0faf206f1f84e59a952f37a9dfc3c2d", "dataset_id": "9f2c1a",
 "steps": ["lowercase", "tokenize", "stopwords"], "level": "info",
 "logger": "sentiment_prep.api.app", "timestamp": "2026-09-05T16:43:53.318Z",
 "message": "request_finished"}
```

- `message` is a snake_case **event name**, stable across releases, so you can filter on it.
- `request_id` is on every line for that request, including lines from SQLAlchemy, boto3 and httpx.
- `dataset_id`, `steps`, `source` appear once the handler binds them; they stay bound for the rest
  of the request.

## Finding a failure from the UI

Error notices in the UI show `request <id>`. Paste it into CloudWatch Logs Insights:

```
fields @timestamp, message, level, logger, duration_ms, error
| filter request_id = "b0faf206f1f84e59a952f37a9dfc3c2d"
| sort @timestamp asc
```

Other useful queries:

```
# Slow requests
fields @timestamp, path, duration_ms | filter message = "request_finished" and duration_ms > 5000

# X spend today
fields @timestamp, reads, reads_today, estimated_cost_usd | filter message = "x_reads_consumed"

# Every enrichment that degraded
fields @timestamp, request_id, message | filter message in ["comprehend_comparison_failed",
  "embedding_drift_failed", "explanation_failed"]

# Cold starts
fields @timestamp, duration_ms | filter message = "database_ready"
```

Locally, `make dev-api` prints coloured lines; pipe through `grep request_id=<id>` or set
`LOG_LEVEL=DEBUG` to see per-page X fetch events and `history_*` writes.

## Event catalogue

| Event | Where | Fields |
|---|---|---|
| `request_started` / `request_finished` / `request_crashed` | middleware | method, path, status, duration_ms |
| `request_rejected` | AppError handler | error, status |
| `dataset_stored` | routes | records, source |
| `x_fetch_started` / `x_page_received` / `x_fetch_complete` | sources.x_search | query, requested, returned, reads_billed |
| `x_fetch_truncated_by_cap` / `x_rate_limited` | sources.x_search | reason / attempt, wait_seconds |
| `x_reads_consumed` | spend_guard | reads, released, reads_this_fetch, reads_today, estimated_cost_usd |
| `spend_reservation_refused` | history.services | day, reads, cap |
| `client_disconnected_during_fetch` | routes | (request context) |
| `hugging_face_fetch_complete` / `csv_parsed` | sources | returned, skipped_empty |
| `step_applied` | preprocessing.base | all StepResult fields except sample_diffs |
| `pipeline_complete` | preprocessing.pipeline | steps, records_in, records_out, duration_ms |
| `sentiment_comparison` | analysis.comprehend_scorer | agreement, distributions |
| `embedding_drift` | analysis.embeddings | sample, mean_cosine_distance |
| `explanation_generated` | analysis.bedrock_explainer | chars |
| `*_failed` | api.service | exc_info |
| `comprehend_batch_scored` / `comprehend_labels_applied` | analysis / labeling | documents / records, units, cost_usd |
| `comprehend_price_fetched` / `comprehend_price_lookup_failed` / `comprehend_price_not_found` | pricing | sku, price / no provider details / region |
| `manual_labels_applied` / `review_sample_chosen` | labeling | records / mode, size |
| `checkpoint_saved` / `checkpoint_failed` | storage.checkpoints | stage, format, bytes, uri |
| `dataset_saved` / `bundle_saved_to_s3` | storage | uri, records / dataset_id |
| `database_ready` / `app_ready` | startup | duration_ms / runtime, version |

## Rules

1. Never log record text above DEBUG. Posts are personal data; CloudWatch is not the place for them.
2. Log at the boundary: one line per external call (service, operation, duration, outcome), one
   per step, one per request. Do not log inside loops over records.
3. Errors from optional enrichments are `logger.error(..., exc_info=True)` **and** a warning in the
   response. Both, always: the operator needs the trace, the user needs to know a field is null.
4. Bind, don't pass. If a value should appear on several lines, `bind_context(key=value)` once.

## Debugging checklist

- 503 with "No X bearer token": check `X_BEARER_TOKEN` (local) or that the Lambda role can read the
  SSM path (`ssm:GetParameter` on the exact ARN). `GET /health` shows `x_configured`.
- Fewer records than requested: read `truncated_reason` in the response; `x_fetch_complete` has
  the same value plus `reads_billed`.
- `report.sentiment` is null: `report.warnings` names the cause; search
  `comprehend_comparison_failed` for the trace. Usually a missing IAM action or an unsupported region.
- History tab empty after a deploy: `database_ephemeral: true` in `/health` means SQLite on `/tmp`.
  See [deployment.md](deployment.md) for Postgres.
