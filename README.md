# Sentiment Prep

Prepare a text dataset for sentiment analysis and measure what each preprocessing step does to
it. FastAPI on AWS Lambda, React UI, one CloudFormation (SAM) stack, structured logging
throughout.

![Collect stage: search a topic](docs/images/1-collect.png)

A guided five-stage flow:

1. **Collect.** Search any topic on X (recent or historical dates, spend-capped), load a labelled sample dataset,
   or upload a CSV.
2. **Clean.** Choose cleaning steps (case, punctuation), NLP normalisation steps
   (tokenize, stopwords, lemmatize), and a final empty-record sweep, each with its trade-offs stated inline.
3. **Analyze.** Run it and read the impact in a sortable, filterable AG Grid table: vocabulary and token changes, a per-step waterfall,
   prediction consistency and sentiment distributions (Amazon Comprehend), and word-level diffs
   for changed posts. Agreement does not establish accuracy or preserved meaning.
4. **Label.** Comprehend labels every post (live-priced estimate shown and confirmed first, resumable, checkpointed
   after each slice); then review low-confidence predictions first, a random sample (count or percent), all, or none with keyboard
   shortcuts. The app reports reviewer-vs-Comprehend agreement.
5. **Export.** Parquet, CSV, Excel, a Markdown report for the write-up, S3 save, and stage
   checkpoints (collected / processed / labelled) with one-click Parquet conversion.

Responsive from phones to desktops, dark and light themes, cancellable requests, run history,
and a live X spend counter. React 19 + TypeScript + Tailwind CSS v4 + AG Grid Community, with
API types generated from the backend's OpenAPI schema and every response validated with Zod.

![Analyze stage](docs/images/3-analyze.png)

## Quick start

```bash
make setup        # backend deps + NLTK corpora + frontend packages + backend/.env
make dev          # API on :8000 (Swagger at /docs) and UI on :5173, together
make stop         # stop this project's local servers, including Vite fallback ports
```

`make` on its own lists every target, grouped. The UI proxies `/api` to the backend and says so
plainly if it cannot reach it. See [docs/deployment.md](docs/deployment.md#troubleshooting) if a `make` target
cannot find `uvicorn` or Docker.

Local CSV processing needs no paid services: Comprehend is off and no X token is set. Upload a CSV, or load the
Hugging Face dataset over the internet; AWS-backed fields are null with a stated reason.

```bash
make check        # lint, types and both test suites; S3 via moto, AWS ML services via fakes, history on SQLite
```

## Reusing a collected dataset

In local development (`RUNTIME=local`), choose **Saved datasets** in Collect. The dropdown lists
working JSON files from `backend/data/checkpoints/_work/` (or `CHECKPOINT_DIR/_work`) by latest save,
newest first. Choose a file and click **Open in Clean**. Older files load in pages of 50; Refresh
updates the list. This picker and its API are unavailable in the deployed runtime.

New JSON filenames use the search topic and the browser's local creation time, for example
`iphone-duo-2026-09-23_12-15-30-UTC-0500.json`. They live in dataset-ID folders within `_work`
to avoid collisions; older flat JSON files remain available. Export uses the same default
name for downloads and S3 data. Select **Custom filename** beside a format to change the name;
the extension is fixed. Each S3 save gets its own folder and retains earlier saves.

Opening a file creates a fresh dataset and starts Step 2 with all options unchecked. Original
rows and source labels are retained; later labels, cleaning, analysis, review, and billing state
are reset. The saved file remains unchanged. **Download original dataset** still provides a
portable JSON archive, but Saved datasets no longer accepts browser uploads.

**Upload CSV** offers drag and drop or a keyboard-accessible file chooser. It checks the complete
file and shows a preview before enabling **Import CSV**. A `text` header is required; `id` and
`label` are optional. Use UTF-8, at most 4 MiB and 5,000 data rows. Empty text rows are reported
and skipped; a file with no usable text is rejected. The UI warns when fewer than 500 posts are
available. The same server parser validates the file again on import.

## Configuration

All settings are environment variables (`.env` locally, SAM template in Lambda). The ones people
change:

| Variable | Purpose |
|---|---|
| `X_BEARER_TOKEN` | X API token for local use. **Change this line to switch accounts.** |
| `X_BEARER_TOKEN_SSM_PATH` | SSM SecureString path used in Lambda (set via `samconfig.toml`) |
| `X_MAX_READS_PER_FETCH`, `X_MAX_READS_PER_DAY` | hard caps on billed reads (defaults 1000 / 3000) |
| `AWS_PROFILE` | named profile from `~/.aws/config`, including SSO; blank uses the default chain |
| `COMPREHEND_ENABLED` | enable paid sentiment scoring |
| `PRICING_ENABLED` | fetch the live Comprehend rate from the AWS Price List API for the estimate (24 h fresh + 48 h stale grace; no hard-coded fallback) |
| `API_KEY` | operator secret exchanged for a temporary browser session; scripts may use `X-API-Key`; required for internet-facing deployments |
| `DIAGNOSTICS` | expose operator-only details in `/health` and the UI (defaults on locally, off in Lambda) |
| `CHECKPOINT_DIR` | local folder for stage snapshots when no bucket is set (gitignored) |
| `DATABASE_URL` | shared PostgreSQL required for paid X collection in Lambda; local default SQLite |
| `DATA_BUCKET` | S3 bucket for saves and Lambda working state |

## Documentation

The [`docs/`](docs/README.md) folder is written so a new engineer can contribute on day one:

- [How the app works](docs/how-the-app-works.md), a walk from click to file.
- [Architecture diagrams](docs/architecture-diagrams.md), [Architecture](docs/architecture.md) and [Decisions](docs/decisions.md).
- [Developer guide](docs/developer-guide.md): setup, conventions, adding a step or source, PR checklist.
- [Logging and debugging](docs/logging-and-debugging.md): structlog events and CloudWatch queries.
- [API reference](docs/api.md) and [Deployment](docs/deployment.md) (secrets, least-privilege IAM).
- [NLP and sentiment primer](docs/nlp-sentiment-primer.md) and [Data cleaning guide](docs/data-cleaning-guide.md).
- [Design system](docs/design-system.md): glass tokens, selected and focus states, contrast verification.

## Deploying

```bash
make put-secret NAME=api-key VALUE='<operator-secret>'
make put-secret NAME=x-bearer-token VALUE='AAAA…'
make deploy                  # infrastructure/stack_request/template.yaml
make deploy-web             # build UI against ApiUrl, sync to the site bucket
```

Full details, including the IAM statement list and the Postgres option, in
[docs/deployment.md](docs/deployment.md).

## Project layout

```
backend/src/sentiment_prep/ api · sources · preprocessing · analysis · labeling · export · storage · history
frontend/src/          React 19 + TypeScript + Vite + Tailwind; api/ hooks/ components/stages/
infrastructure/        stack_request/ (template.yaml, samconfig.toml)
backend/tests/         unit/ and integration/
docs/                  guides and screenshots
```

## Code style

[Broad Institute coding and comment style](https://mitcommlab.mit.edu/broad/commkit/coding-and-comment-style/):
module docstrings state purpose and the one thing a reader must know; comments explain *why*;
names are full words; functions do one thing. Enforced by `ruff` (pydocstyle, naming) and
`mypy --strict`.
