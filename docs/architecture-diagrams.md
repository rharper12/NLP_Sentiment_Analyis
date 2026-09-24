# Architecture diagrams

These diagrams describe the current implementation. GitHub renders the Mermaid blocks directly.
See [architecture.md](architecture.md) for storage and request-lifecycle details.

## 1. Deployment

```mermaid
flowchart TB
    accTitle: AWS deployment
    accDescr: The browser loads the UI through CloudFront and calls API Gateway and Lambda; the backend uses S3, PostgreSQL, SSM, and configured external services.
    Browser["Browser"] --> CDN["CloudFront<br/>HTTPS frontend"]
    CDN --> Site["Private S3 site bucket<br/>React production build"]
    Browser --> Gateway["API Gateway HTTP API"]
    Gateway --> API["Lambda container<br/>FastAPI"]
    API --> Data["S3 data bucket<br/>Working bundles, checkpoints, exports"]
    API --> DB["PostgreSQL<br/>Shared history and X spend ledger"]
    API --> Secrets["SSM Parameter Store<br/>Operator key, X token, database URL"]
    API --> X["X API"]
    API --> Comprehend["Amazon Comprehend"]
    API --> Pricing["AWS Price List API"]
    API --> Sample["Hugging Face datasets-server"]
```

Paid X collection in Lambda requires the shared PostgreSQL ledger. SQLite on Lambda's `/tmp`
is only an ephemeral fallback for history when paid X collection is not in use. Locally, Vite
proxies the API during development, working bundles use local JSON files, and history defaults
to SQLite.

## 2. Dataset workflow

```mermaid
flowchart TD
    accTitle: Dataset dependencies
    accDescr: Original records feed preprocessing and labelling; exports join original text, processed text, and current labels by record ID.
    Collect["Collect<br/>X, sample, CSV, or local saved original"] --> Original["Original records"]
    Original --> Clean["Choose optional preprocessing steps"]
    Clean --> Analyze["Run preprocessing and inspect changes"]
    Analyze --> Processed["Processed records and impact report"]
    Analyze -->|Adjust steps| Clean
    Original --> Label["Optional Comprehend labels<br/>and manual review"]
    Label --> Annotations["Latest labels and their sources"]
    Original --> Export["Join by record ID for export"]
    Processed --> Export
    Annotations --> Export
```

The UI presents Collect, Clean, Analyze, Label, and Export in sequence. This diagram shows the
data dependencies: labelling reads original text, while Analyze can compare predictions for both
representations. Reprocessing starts from original text rather than repeatedly cleaning an
already processed version.

## 3. Module responsibilities

```mermaid
flowchart TD
    accTitle: Module responsibilities
    accDescr: API routes coordinate injected dependencies, preprocessing, analysis, labelling, exports, storage, and history services.
    Routes["api/routes.py<br/>Request validation and orchestration"] --> Deps["api/deps.py<br/>Configured clients and repositories"]
    Routes --> Service["api/service.py<br/>Preprocessing and analysis progress"]
    Routes --> Labels["labeling/service.py<br/>Labels, review selection, summaries"]
    Routes --> Storage["storage/<br/>Bundles, checkpoints, S3 exports"]
    Routes --> Exports["export/<br/>Shared rows and file encoders"]
    Routes --> History["history/<br/>Runs, spend ledger, pricing cache"]
    Deps --> Sources["sources/<br/>X, Hugging Face, CSV"]
    Service --> Pipeline["preprocessing/<br/>Record transformations"]
    Service --> Analysis["analysis/<br/>Metrics and Comprehend scoring"]
    Labels --> Analysis
    Labels --> Pricing["pricing/<br/>Current rate and cached quotes"]
    Pricing --> History
    Sources --> Guard["SpendGuard<br/>Injected ledger interface"]
    Storage --> Exports
```

The spend guard accepts a ledger interface; dependency construction supplies `DbLedger`.
Sources do not import the concrete history implementation. Shared Pydantic models define records,
bundles, progress, and report shapes without starting clients or reading stored datasets.

## 4. X collection and pagination

```mermaid
sequenceDiagram
    accTitle: X collection progress
    accDescr: The API claims a dataset, reserves reads, retrieves and filters a page, saves the cursor, settles accounting, and repeats while more retained posts are needed.
    participant UI as Browser
    participant API as Collection route
    participant Store as Bundle repository
    participant Guard as Spend guard and SQL ledger
    participant X as X API

    UI->>API: Query, dates, retained-post limit, request ID
    API->>Store: Create or claim the saved collection
    loop While more unique posts are needed and budget remains
        API->>Guard: Reserve reads before the request
        Guard-->>API: Reservation accepted or cap reached
        API->>X: Request page with saved cursor
        X-->>API: Posts and next cursor
        API->>API: Validate, filter, and deduplicate
        API->>Store: Save retained posts and cursor
        API->>Guard: Settle actual reads
        API->>Store: Save accounting progress
    end
    API->>Store: Write collected checkpoint when time permits
    API-->>UI: Retained count, billed reads, and resume status
```

The implementation requests at most 100 posts per page. Filtering happens inside the loop, so
removed posts do not satisfy the retained-post target. A partial response can be resumed with
the same request ID. Collection is not stratified by date.

The sequence shows successful pages. Caps, deadlines, cancellation, invalid responses, and rate
limits can stop a slice. An ambiguous transport failure may already have incurred a charge;
its reservation is retained conservatively. A failed required save prevents another paid page.

## 5. Labels and review

```mermaid
flowchart TD
    accTitle: Label provenance
    accDescr: Source labels and Comprehend predictions can be reviewed manually; manual decisions take precedence while machine predictions remain available.
    Original["Original record"] --> Machine["Optional Comprehend prediction"]
    Original --> Existing["Existing source label, if supplied"]
    Machine --> Stored["Store prediction and confidence separately"]
    Stored --> Choose["Choose review set<br/>Low confidence, random, all, or none"]
    Existing --> Choose
    Choose --> Human["Review original text<br/>Save manual decisions"]
    Existing --> Final["Export label and label_source"]
    Stored -->|Use when no label exists| Final
    Human -->|Manual decision takes precedence| Final
```

Manual decisions preserve the machine prediction for comparison. Pausing or finishing review
waits for pending saves. Reviewer agreement is measured on records with both a manual decision
and a Comprehend result; it is not a model-accuracy score. Low-confidence review intentionally
selects difficult cases and should not be treated as a representative evaluation sample.

## 6. Working files, checkpoints, and exports

```mermaid
flowchart LR
    accTitle: Storage destinations
    accDescr: Working journals are stored locally or in S3 by runtime; stage checkpoints and user-requested S3 export folders have separate lifecycles.
    Bundle["Dataset bundle"] --> Local["Local journal<br/>_work / dataset ID / named JSON"]
    Bundle --> Cloud["Lambda journal<br/>S3 _work / dataset ID.json"]
    Bundle --> Checkpoint["Stage snapshots<br/>collected, processed, labelled"]
    Bundle --> Save["User-requested S3 save<br/>dataset ID / filename / unique save ID"]
    Save --> Parquet["Named Parquet file"]
    Save --> Impact["impact.json"]
    Save --> Manifest["manifest.json"]
```

Only the repository for the configured runtime owns working state. Local edits use filesystem
locks and atomic replacement; S3 edits use conditional writes and an exclusive claim. Stage
snapshots can be replaced as work progresses. User-requested S3 exports use a fresh UUID folder
for each save, including when the filename repeats.
