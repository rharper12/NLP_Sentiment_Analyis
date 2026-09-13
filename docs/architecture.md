# Architecture

Diagrams for all of this are in [architecture-diagrams.md](architecture-diagrams.md).

```
Browser (React 18, Vite)  ──HTTPS──►  API Gateway (HTTP API)  ──►  Lambda container
                                                                     │
                                       FastAPI (ASGI root) ──────────┤── history/ (SQLAlchemy)
                                       │                             │      runs, spend ledger
                                       ├─ sources/      X · HF · CSV │
                                       ├─ preprocessing/ 6 steps     │
                                       ├─ analysis/     metrics · Comprehend · Titan · Bedrock
                                       ├─ export/       csv · xlsx · parquet
                                       └─ storage/      bundle repository · S3 saves
                                                                     │
                       S3 data bucket ◄──────────────────────────────┤──► SSM (secrets)
                       SQLite (/tmp) or Postgres via DATABASE_URL ◄──┘
```

## One framework

FastAPI owns HTTP: typed request/response models, automatic OpenAPI, async where it matters
(client-disconnect detection). Persistence for the two small history tables is SQLAlchemy 2.0,
which needs no second framework: typed models, a session context manager, and ``create_all`` on
first use. An earlier iteration mounted Django for its ORM and admin; it was removed because a
second framework's settings, middleware, migration runner and static-file handling cost far more
than an admin screen was worth (see ADR-11).

## Layers and their rules

| Layer | Depends on | Must not |
|---|---|---|
| `models.py` | pydantic only | import anything else in the package |
| `sources/` | models, logging, spend_guard | know about HTTP routes or storage |
| `preprocessing/` | models, logging, nltk | do I/O or call AWS |
| `analysis/` | models, logging, boto3 clients passed in | construct its own clients |
| `labeling/` | models, analysis.comprehend_scorer, config, pricing | do I/O beyond the Comprehend client it is given |
| `pricing/` | history.services (cache), boto3 client passed in | fall back to a hard-coded price |
| `export/`, `storage/` | models, export.rows | know about routes |
| `storage/checkpoints.py` | export.csv_export, boto3 client passed in | fail a request when a snapshot cannot be written |
| `history/` | SQLAlchemy, models, config | be imported by anything other than `api/` and `deps` |
| `api/` | everything above | contain business logic beyond orchestration |

Clients (boto3, httpx) are always passed in, never created inside the module that uses them.
That is what makes every module testable with fakes and `moto`.

## Data model

`Record → Dataset → DatasetBundle` (`models.py`). A bundle is the unit of storage: the original
dataset, the latest processed dataset, the steps that produced it, and the `ImpactReport`.
Preprocessing always starts from `bundle.original`, so toggling steps is idempotent.

`StepResult` is produced by every step; `ImpactReport` collects them plus optional enrichments
and a `warnings` list that explains any `None`.

History tables (`history/models.py`) store metadata only: `DatasetRun`, `PipelineRun`, `SpendEntry`.
Record text never enters the database or the logs above DEBUG.

## Process lifecycle

Importing `api.app` has no side effects. The `lifespan` handler creates the history schema on
startup and closes the pooled HTTP and AWS clients on shutdown; Mangum runs it with
`lifespan="auto"`. HTTP clients (`httpx`) and boto3 clients are cached per process in
`api/deps.py` and injected — nothing constructs a client per request.

## Request lifecycle

1. Middleware clears the logging context, binds `request_id`, `method`, `path`.
2. Route handler runs (sync handlers in the threadpool; `load_dataset` is async so it can watch
   for disconnects while the fetch runs in a worker thread).
3. `AppError` subclasses map to 4xx/503 with a JSON body that includes the request id; anything
   else becomes a 500 with the same shape.
4. Middleware logs `request_finished` with status and duration and echoes `X-Request-Id`.

## Stateless compute

Lambda keeps nothing between invocations. Working bundles go to S3 under `_work/` (7-day
lifecycle) when `RUNTIME=lambda`; locally they stay in memory. History uses SQLite on `/tmp` by
default in Lambda, which survives warm invocations only; the health endpoint reports
`database_ephemeral: true` so the UI can say so. Point `DATABASE_URL_SSM_PATH` at a Postgres URL
to make history durable.

## Cost controls

Money is `Decimal` end to end on the server: the Price List price is parsed from AWS's string
without passing through `float`, `cost_for_units` is the single place it is multiplied, and the
value becomes a `float` only in the JSON response. Comprehend labelling is priced before it runs (`labels/estimate`) from the live Price List rate, refused without an explicit
`confirm_cost`, done in resumable slices that skip already-labelled records, and checkpointed after
every slice. The same `ComprehendScorer` serves the Analyze comparison, with a per-instance cache
keyed on text hash.

X reads are the only variable cost. `SpendGuard` reserves reads before each page and refuses when
either cap would be exceeded; the ledger is append-only in the database so concurrent invocations
cannot undercount. Comprehend results are cached by text hash per scorer instance; embedding drift
uses a fixed sample of 50; the explainer receives numbers only.
