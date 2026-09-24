# Architecture

Sentiment Prep separates record transformations from HTTP, storage, and paid service calls.
The [Mermaid diagrams](architecture-diagrams.md) show deployment, workflow, dependencies,
collection progress, labels, and exports.

## Components

| Component | Responsibility |
| --- | --- |
| React frontend | Five-stage workflow, request cancellation, accessible controls, review, and downloads. |
| FastAPI | Request validation, authentication, service orchestration, OpenAPI, and errors with request IDs. |
| `sources/` | X pagination, Hugging Face paging, CSV validation, deduplication, and read-budget enforcement. |
| `preprocessing/` | Transform records without modifying the original input or calling external services. |
| `analysis/` | Compute dataset metrics and compare Comprehend predictions for original and processed text. |
| `labeling/` | Label original text, choose review records, apply manual decisions, and summarize coverage. |
| `pricing/` | Obtain Comprehend rates, using the history database for cached quotes. |
| `export/` | Join records by ID and encode CSV, Excel, and Parquet consistently. |
| `storage/` | Persist working bundles, track checkpoint freshness, and write user-requested S3 exports. |
| `history/` | SQLAlchemy models and transactions for run history, spend accounting, and pricing cache. |
| `api/deps.py` | Construct and cache the clients and repositories used by the other components. |

The runtime dependency direction is toward shared models and service interfaces. For example,
`SpendGuard` accepts a ledger interface; `api/deps.py` supplies the SQL-backed implementation.
The pricing service uses the history cache, and labelling updates checkpoint freshness metadata.
These are explicit dependencies, not exceptions hidden behind HTTP routes.

## Data model

`Record` holds text, source metadata, optional tokens, and label provenance. `Dataset` groups
records with collection details. `DatasetBundle` combines the original and processed datasets,
selected steps, the impact report, review selection, checkpoint status, and resumable job state.

Preprocessing always starts from `bundle.original`. It replaces the processed representation,
so changing options does not repeatedly transform previously cleaned text. Labelling updates
annotations on original records without rewriting their text. Exports join both representations
by record ID, including originals whose processed record was dropped.

`label` is the current selected label and `label_source` identifies its origin. A manual decision
takes precedence. `comprehend_label` and `comprehend_confidence` remain available for comparison.
A human-reviewed subset is not automatically an unbiased evaluation set; low-confidence review
is deliberately selective, and training/evaluation splits belong to a subsequent project.

## Storage and process lifecycle

| Data | Local runtime | Lambda runtime |
| --- | --- | --- |
| Working bundle | `CHECKPOINT_DIR/_work/<dataset-id>/<file-stem>.json`; legacy flat JSON remains readable. | S3 `_work/<dataset-id>.json`. |
| Stage checkpoints | Local `CHECKPOINT_DIR`, or S3 when `DATA_BUCKET` is set. | S3 under `CHECKPOINT_PREFIX`. |
| Run history and pricing cache | SQLite by default; configurable through `DATABASE_URL`. | PostgreSQL, or ephemeral `/tmp` SQLite when no database is configured. |
| Paid X spend ledger | Database-backed, using local SQLite or the configured database. | Shared PostgreSQL is required; collection refuses an ephemeral ledger. |
| User-requested S3 exports | Optional `DATA_BUCKET`. | `DATA_BUCKET`. |

Local working files use an OS lock for each dataset and atomic file replacement. S3 working
bundles use conditional writes and a claim that spans paid work and persistence. An abandoned
S3 claim requires operator investigation; it does not expire automatically and permit uncertain
work to be billed again. A provider response lost before a required save still creates an
unavoidable billing ambiguity.

Working journals are updated in place. Stage CSV snapshots are updated after collect,
preprocessing, and labelling; failed or outdated snapshots are marked in the UI. Each explicit
S3 export gets a separate folder:

```text
<DATASET_PREFIX>/<dataset-id>/<file-stem>/<save-uuid>/
  <file-stem>.parquet
  impact.json
  manifest.json
```

The manifest is written last and describes the export. The three S3 writes are not one atomic
transaction; a failed save can leave an incomplete folder. A retry creates a new folder.

HTTP and AWS clients are owned by the dependency layer. Local ASGI lifespan initializes the
history schema and closes clients on shutdown. Lambda uses Mangum with lifespan disabled;
database initialization is lazy, and warm invocations reuse clients. Durable state lives outside
the Lambda process. SSM secrets are cached for five minutes, and SQL sessions close per transaction.

## Requests and progress

1. Middleware attaches a request ID and a bounded request budget.
2. Authentication accepts an operator key or an expiring browser session. Lambda fails closed
   when its operator credential is unavailable.
3. Synchronous routes use FastAPI's worker threads. Upload handlers read bounded multipart data
   asynchronously, then move parsing and persistence to a worker. Collection watches for disconnects.
4. Operations that edit existing bundles claim the dataset before performing paid work.
5. Application errors return an appropriate status and request ID. Unexpected errors are logged
   and return a generic error response.

The browser validates API responses with Zod and cancels obsolete requests when the user changes
configuration or datasets. Cancellation does not undo work already committed by the server.
Manual review uses an ordered save queue; pausing and finishing wait for pending decisions.

## Cost controls

X reads are reserved before each page. Successful responses settle all returned posts, including
posts later removed by filtering. Filtering and deduplication run before checking the retained
record target. Rate limits, spend caps, and request deadlines produce explicit stop or resume
states. The saved cursor and search bounds keep resumed pages tied to the same collection.

Comprehend labelling requires cost confirmation and runs in resumable batches. Analyze's optional
before/after comparisons also call Comprehend when enabled and cache results by text hash.
Successful progress is saved before the next paid batch. Temporary failures have bounded retries;
missing results are not counted as sentiment agreement.

Pricing uses `Decimal` on the server and reports an unavailable estimate when a valid rate cannot
be obtained. The X estimate uses the configured per-read rate; actual provider billing remains
the source of truth.
