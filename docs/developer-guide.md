# Developer guide

## First hour

```bash
git clone <repo> && cd sentiment-prep
make setup          # Python deps, NLTK corpora, npm packages, .env from .env.example
make test           # 40 tests, no AWS credentials needed
make local-api      # http://localhost:8000/docs
make frontend       # http://localhost:5173 (proxies /api to :8000)
```

With the default `.env`, Comprehend and Bedrock are off and no X token is set. Load the Hugging
Face dataset or upload a CSV and the whole pipeline works offline. Turn services on one at a time
as you get credentials.

## Repository map

```
src/sentiment_prep/
  api/            FastAPI: app.py (factory, middleware, SQLAlchemy mount), routes.py, schemas.py,
                  service.py (orchestration), deps.py (client construction)
  sources/        DataSource adapters + SpendGuard
  preprocessing/  one module per step, pipeline.py, STEP_REGISTRY in __init__.py
  analysis/       metrics, Comprehend (shared scorer), Titan embeddings, Bedrock explainer
  labeling/       estimate, Comprehend slices, review sampling, manual labels, summary
  export/         csv, xlsx; rows.py is the shared row shape
  storage/        repository (in-memory | S3), checkpoints (local | S3), s3_store (saves)
  history/        SQLAlchemy app: models, admin, services (incl. SQLAlchemyLedger)
  sqlalchemy_project/ settings, urls, bootstrap
  resources/      rationale.yaml, explain_prompt.txt
  config.py       every env var; logging_config.py: structlog setup; models.py; report.py
frontend/src/     api/ (client, types), hooks/, components/ (stages/, ui/), App.tsx, styles.css (Tailwind theme)
                  RecordsGrid.tsx wraps AG Grid Community; see docs/design-system.md
tools/            a11y_audit.py: drives every stage in both themes and runs axe (WCAG A/AA)
infrastructure/stack_request/   template.yaml, samconfig.toml, env.local.json
tests/            unit/ mirrors src; integration/ hits routes via TestClient
docs/             you are here
```

## Conventions

**Code and comments** follow the Broad Institute style guide. In practice:

- Every module opens with a docstring stating what it is for and the one thing a reader must
  know before editing it. Public functions get a one-line docstring; interface implementations
  inherit documentation from the base class (`D102` is disabled for that reason).
- Comments explain *why*. If a comment restates the code, delete it. If a decision is surprising,
  say what would go wrong the other way.
- Names are full words. `dataset_id`, not `dsid`. Functions do one thing and fit on a screen.
- No commented-out code. No `print`. No bare `except` outside the enrichment boundary in
  `service.py`, where it is annotated with why.

**Types.** `mypy --strict` passes. Use `Any` only at boto3/httpx boundaries and say so.

**Logging.** `log.info("event_name", key=value)`. Event names are snake_case identifiers, not
sentences. Never log record text above DEBUG. See [logging-and-debugging.md](logging-and-debugging.md).

**Errors.** Raise `ValidationError`, `NotFoundError`, or `ConfigurationError` from `errors.py`.
They become clean 4xx/503 responses. Everything else is a bug and becomes a 500 with a request id.

**Frontend types are generated, never hand-written.** `src/api/schema.d.ts` comes from the
backend's OpenAPI document (`make gen-api` with the API running) and `src/api/types.ts` derives
every exported type from it. If you change a Pydantic schema, regenerate: a field that became
optional shows up as a type error instead of `undefined` at runtime.

**Frontend.** Tailwind CSS v4 utilities referencing theme tokens (`bg-surface`, `text-muted`,
`border-rule`); the tokens are CSS variables in `styles.css` and dark mode swaps their values, so
no component knows which theme is active. Function components and hooks only. State that must survive re-renders lives in
`useState`/`useReducer`; server data goes through `useAsync`, which owns loading, error, and
cancellation. Every list has stable keys. Every form control has a label. Components take props;
they do not reach into global state.

## Adding a preprocessing step

1. Create `preprocessing/my_step.py` with a class extending `PreprocessStep`, a `name: ClassVar[str]`,
   and a `transform(record) -> Record | None`. Return a new record via `model_copy`; never mutate.
   If your step changes the word list, set both `tokens` and `text=" ".join(tokens)`.
2. Register it in `preprocessing/__init__.py` (`STEP_REGISTRY` and, if it belongs in the default
   order, `DEFAULT_ORDER`).
3. Add an entry to `resources/rationale.yaml` with `title`, `summary`, `strengths`, `limitations`.
   Be honest in `limitations`; that text ends up in the report a grader reads.
4. If the step needs options, add them to `StepOptions` in `api/schemas.py` and wire them in
   `service.py::build_steps`, then expose the control in `components/stages/CleanStep.tsx` and add the step to a group in
   `GROUPS` there and `GROUP_OF` in `hooks/usePipelineConfig.ts`.
5. Tests in `tests/unit/test_preprocessing.py`: normal text, empty string, non-ASCII, text that is
   entirely removed. The UI needs no changes for a plain step; it reads the catalogue from `/steps`.

## Adding a data source

1. Create `sources/my_source.py` implementing `fetch(limit, query=None, should_stop=None) -> Dataset`.
   Return partial results with `truncated_reason` instead of raising. Poll `should_stop()` between
   pages if the source is paged.
2. If it costs money, take a `SpendGuard` in the constructor and call `reserve` before and `record`
   after each request.
3. Add the source name to `SourceType` in `models.py` and `LoadRequest.source` in `schemas.py`,
   construct it in `api/deps.py`, and branch on it in `routes.py::load_dataset`.
4. Add a tab to the source switcher in `components/stages/CollectStep.tsx`.
5. Test against `httpx.MockTransport`; see `tests/unit/test_sources.py` for the pattern.

## Testing

- `pytest -q` runs everything in about seven seconds. S3 uses `moto`; Comprehend and Bedrock use
  the fakes in `tests/conftest.py`; history uses a fresh SQLite file under `/tmp` each session.
- Integration tests build the app with `create_app()` and monkeypatch `api.deps` functions. Do the
  same for a new external dependency rather than reaching for network mocks.
- Keep a test that exercises every `None` path in `ImpactReport`.

## Before opening a PR

```bash
make lint && make test
```

`make lint` runs ruff, `mypy --strict`, `tsc --noEmit` and ESLint (with `react-hooks` and
`jsx-a11y`). `make test` runs pytest and Vitest. Before shipping a UI change, also run the
accessibility audit in `tools/a11y_audit.py`; see [design-system.md](design-system.md).

Then check: docstrings on new modules, no record text in INFO logs, rationale updated if a step
changed, `docs/decisions.md` updated if you changed a boundary in [architecture.md](architecture.md).
