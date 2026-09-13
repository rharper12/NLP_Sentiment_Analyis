# Architecture diagrams

Five views, from the outside in. Every diagram renders on GitHub without a plugin.
Prose and the layer dependency rules live in [architecture.md](architecture.md); this file is the
visual companion.

## 1. System context

Who talks to what, and which parts cost money.

```mermaid
flowchart LR
    person(["Person preparing a dataset"])

    subgraph aws["AWS account"]
        cf["CloudFront-less S3 site<br/>(static React bundle)"]
        gw["API Gateway<br/>HTTP API, throttled"]
        fn["Lambda<br/>FastAPI container"]
        s3[("S3<br/>datasets · checkpoints")]
        db[("SQLite on /tmp<br/>or Postgres")]
        ssm["SSM Parameter Store<br/>API key · X token"]
    end

    x["X API v2<br/>$0.005 per post read"]
    comp["Amazon Comprehend<br/>$0.0001 per 100 chars"]
    bed["Amazon Bedrock<br/>Titan embed · Claude"]
    price["AWS Price List API<br/>free"]
    hf["Hugging Face<br/>datasets-server, free"]

    person --> cf --> gw --> fn
    fn --> s3
    fn --> db
    fn --> ssm
    fn -->|paid| x
    fn -->|paid| comp
    fn -->|paid| bed
    fn --> price
    fn --> hf

    classDef paid fill:#fde2e1,stroke:#b42318,stroke-width:2px;
    class x,comp,bed paid
```

Red edges cost money. Everything red is capped, estimated before it runs, and checkpointed after.

## 2. The five stages, and what each one persists

```mermaid
flowchart TD
    A["1 · Collect<br/>search a topic, or load a sample"] --> B["2 · Clean<br/>choose and order the steps"]
    B --> C["3 · Analyze<br/>run, then measure the change"]
    C --> D["4 · Label<br/>Comprehend, then human review"]
    D --> E["5 · Export<br/>Parquet · CSV · Excel · report"]

    A -.writes.-> ck1[("checkpoint: collected.csv")]
    C -.writes.-> ck2[("checkpoint: processed.csv")]
    D -.writes after every slice.-> ck3[("checkpoint: labelled.csv")]
    E -.on request.-> save[("s3://…/datasets/{id}/{timestamp}/")]

    A -->|filtered at collection| drop["not English · no content ·<br/>duplicate · near-duplicate"]

    classDef note fill:#fff6e0,stroke:#7a4b00;
    class drop note
```

Dropping contentless and duplicate posts **at collection** is deliberate: it fixes the record
count, so two preprocessing configurations are compared over identical rows.

## 3. Module dependencies

Arrows point the way imports go. Nothing points back up.

```mermaid
flowchart TD
    routes["api/routes.py"] --> service["api/service.py"]
    routes --> labeling["labeling/service.py"]
    routes --> deps["api/deps.py<br/>builds and caches clients"]
    routes --> exp["export/"]
    routes --> ckpt["storage/checkpoints.py"]
    routes --> hist["history/services.py"]
    routes --> rep["report.py"]

    service --> pre["preprocessing/"]
    service --> ana["analysis/"]
    labeling --> ana
    labeling --> pricing["pricing/"]
    pricing --> hist
    deps --> sources["sources/"]
    sources --> guard["sources/spend_guard.py"]
    sources --> dedupe["sources/dedupe.py"]
    guard --> hist

    pre --> models["models.py"]
    ana --> models
    exp --> models
    ckpt --> exp

    classDef base fill:#e4ecfb,stroke:#1f5fe0;
    class models base
```

`models.py` depends on nothing but Pydantic, which is what lets every other module be tested
without a network, a database or an AWS account.

## 4. One paid request, end to end

The X fetch, because it is the path where a mistake costs real money.

```mermaid
sequenceDiagram
    participant UI as React UI
    participant API as FastAPI route
    participant G as SpendGuard
    participant DB as spend_day (SQL)
    participant X as X API v2
    participant CK as Checkpoint store

    UI->>API: POST /dataset/load {query, limit}
    loop until limit, or a cap, or the client disconnects
        API->>G: reserve(page_size)
        G->>DB: UPDATE … WHERE reads + n <= cap
        alt no rows updated
            DB-->>G: cap reached
            G-->>API: SpendCapReachedError → partial dataset
        else reserved
            API->>X: GET /2/tweets/search/recent
            X-->>API: up to 100 posts (billed)
            API->>G: record(actual)
            G->>DB: audit row + release the unused reservation
        end
    end
    API->>API: filter non-English, contentless, duplicates
    API->>CK: write collected.csv
    API-->>UI: summary + why posts were dropped
```

The reservation is claimed **before** the request goes out and released after, so two concurrent
fetches cannot both spend the last of the day's budget.

## 5. How a label is produced, and where it comes from

```mermaid
stateDiagram-v2
    [*] --> unlabelled: collected from X
    [*] --> source_labelled: came with the dataset

    unlabelled --> comprehend: Comprehend slice (paid, resumable)
    source_labelled --> comprehend_compared: Comprehend runs anyway,<br/>stored beside the existing label

    comprehend --> manual: chosen for review
    comprehend_compared --> manual: chosen for review
    comprehend --> [*]: exported, label_source = comprehend
    source_labelled --> [*]: exported, label_source = source
    manual --> [*]: exported, label_source = manual

    note right of manual
        A manual label always wins, and never
        erases comprehend_label — the two
        together give the agreement figure
        the write-up quotes.
    end note
```

Task 2 trains on `label` and evaluates on the rows where `label_source = manual`, which is the
only subset a human has confirmed.
