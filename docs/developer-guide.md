# Developer guide

## First hour

```bash
git clone https://github.com/rharper12/NLP_Sentiment_Analyis.git
cd NLP_Sentiment_Analyis
python3 -m venv .venv
make setup          # Python deps, NLTK corpora, npm packages, .env from .env.example
make test           # backend/frontend regression suites; no AWS credentials needed
make dev            # API on :8000 and UI on :5173, together
make stop           # stop all this checkout's local servers, including fallback ports
make dev-web       # http://localhost:5173 (proxies /api to :8000)
```

Run `make stop` from another terminal in this checkout to stop its API and Vite servers,
including leftover instances on fallback ports. It also handles jobs suspended with Ctrl-Z
and force-stops processes that do not exit within three seconds. Other checkouts and unrelated
applications are left running. Ctrl-C in the `make dev` terminal also stops both servers.

With the default `.env`, Comprehend is off and no X token is set. CSV processing
works offline; loading the Hugging Face dataset requires internet access. Turn services on one
at a time as you get credentials.

## Repository map

```
backend/
  src/sentiment_prep/
    api/            FastAPI: app.py (factory, lifespan, middleware), routes.py, schemas.py,
                    service.py (orchestration), deps.py (client construction), security.py
    sources/        DataSource adapters + SpendGuard
    preprocessing/  one module per step, pipeline.py, STEP_REGISTRY in __init__.py
    analysis/       metrics, Comprehend (shared scorer)
    labeling/       estimate, Comprehend slices, review sampling, manual labels, summary
    pricing/        Price List lookup with a 24 h cache and 48 h stale grace
    export/         csv, xlsx, parquet; rows.py is the shared row shape
    storage/        repository (local journal | S3; in-memory test adapter), checkpoints (local | S3), s3_store (saves)
    history/        SQLAlchemy models, db (engine/session), services (incl. DbLedger)
    resources/      rationale.yaml
    config.py       every env var; logging_config.py: structlog; models.py; report.py
  tests/            unit/ mirrors src; integration/ hits routes via TestClient
  pyproject.toml    dependencies, ruff, mypy, pytest
  Dockerfile        Lambda image (built with the repo root as context)
frontend/
  src/api/          schema.d.ts (generated), types.ts (derived), validation.ts (Zod), client.ts
  src/hooks/        useAsync, usePipelineConfig, useTheme
  src/components/   stages/ (one per step of the flow), collect/ (CSV validation, local picker), label/, ui/
infrastructure/stack_request/   template.yaml, samconfig.toml, env.local.json
tools/              a11y_audit.py
docs/               you are here
Makefile            runs both halves; every target cd's into the right folder
```

## Conventions

**Code and comments** follow the [Broad Institute of MIT and Harvard guide](https://mitcommlab.mit.edu/broad/commkit/coding-and-comment-style/).
It emphasizes readable naming, structure, context, and useful comments. The conventions here are
project choices, not a claim of MIT certification:

- Python modules state their purpose in a docstring. Document public interfaces, side effects,
  and constraints callers need to know. Interface implementations can use the base documentation
  (`D102` is disabled); do not add prose just to repeat a function name.
- Comments explain *why*. If a comment restates the code, delete it. If a decision is surprising,
  say what would go wrong the other way.
- Use descriptive names, with familiar short names only in a small, clear scope. Keep functions
  focused. Long orchestration functions deserve particular care around ordering and persistence.
- Use complete sentences in explanatory comments. Remove obsolete claims and commented-out code.
- Application code uses structured logging; command-line tools may print results. Catch specific
  exceptions where possible. Broad catches need a clear boundary, such as rollback, cleanup,
  or optional enrichment, and must preserve required persistence failures.
- Ruff formats Python at 100 columns. Preserve existing language conventions instead of
  reformatting unrelated files. Break long frontend expressions into readable blocks when editing them.

**Linting is the contract, not a habit.** ruff enforces `A, ASYNC, B, BLE, C4, D, DTZ, E, F, I, N,
PIE, PTH, RET, RUF, SIM, TRY, UP`. `BLE` flags broad catches that are neither handled nor explicitly
suppressed; review their intent as well as the lint result. `DTZ` flags timezone-naive datetimes;
`PTH` keeps filesystem work on `pathlib`. Two rules are switched off deliberately: `TRY003`
(user-facing exception messages are written out in full) and `TRY400` (`logger.error(...,
exc_info=True)` is intentional; `.exception()` would duplicate the message).

**Types.** `mypy --strict` passes. AWS clients are typed with `boto3-stubs`
(`ComprehendClient`, `S3Client`, `PricingClient`) imported under
`TYPE_CHECKING`, so stubs cost nothing at runtime. External payloads require runtime validation;
SDK/ORM boundary casts alone do not validate provider responses.

**Logging.** `log.info("event_name", key=value)`. Event names are snake_case identifiers, not
sentences. Never log record text above DEBUG. See [logging-and-debugging.md](logging-and-debugging.md).

**Errors.** Raise `ValidationError`, `NotFoundError`, or `ConfigurationError` from `errors.py`.
They become clean 4xx/503 responses. Everything else is a bug and becomes a 500 with a request id.

**Tests assert behaviour, not implementation.** Prefer "no further calls were billed" over an
exact call count, "these ids were reviewed" over the order they came back in. A test that pins an
internal constant fails when nothing is actually broken, and then gets deleted rather than fixed.

**API types are generated; runtime validators are maintained separately.** `src/api/schema.d.ts`
comes from OpenAPI (`make api-types` with the API running), and `src/api/types.ts` derives the
frontend's contract types. `api/validation.ts` contains the Zod response validators, checked
against generated types with `z.ZodType<GeneratedType>`. After changing a Pydantic response model,
regenerate the types and update the corresponding validator and tests. Extra response fields are
accepted; missing or invalid required fields produce `ApiContractError` with the failing path.

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
   `service.py::build_steps`, then expose the control in `components/stages/CleanStep.tsx`. Assign its group in backend
   `STEP_GROUPS`; the frontend reads that metadata from `/steps`. Update the shared catalogue
   fixture in `backend/tests/fixtures/steps.json`, which both test suites verify.
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

- `make test` runs both suites; `cd backend && ../.venv/bin/pytest -q` runs the backend suite. Wall-clock
  assertions are marked `slow` and can be skipped with `pytest -m "not slow"`. S3 uses `moto`; Comprehend uses
  the fakes in `tests/conftest.py`. The suite ignores developer `.env` and application environment
  settings; each session gets its own temporary SQLite database and checkpoint directory.
  Tests can explicitly override settings. `TEST_POSTGRES_URL` is retained for the optional test
  against a disposable PostgreSQL database.
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
