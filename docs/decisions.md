# Decisions

Architecture decision records, newest first. Each says what was decided, why, and what it costs.

## ADR-17: The daily spend cap is enforced by the database, not the application
The guard used to read the day's total, decide, then write — so two concurrent fetches could each
pass a check only one should. A `spend_day` counter row is now incremented by a single conditional
`UPDATE … WHERE reads + :n <= :cap`; a zero row count means someone else took the last of the
budget. Reservations are claimed before the request goes out and the unused part is released when
the page returns fewer posts than requested, so a cancelled or failed fetch does not strand budget.
`spend_entry` stays as the append-only audit trail of what was actually billed. Cost: one more
table and a two-step reserve/settle protocol instead of a single `add`.

## ADR-16: React 19, generated API types, and a real frontend toolchain
A code review found the frontend was the weak half: React 18 against a React 19 standard, no
ESLint (so `react-hooks/exhaustive-deps` was not enforced, and it found eight issues on its first
run including two accessibility errors), no tests, and API responses cast with `as T` from types
hand-copied out of the Pydantic schemas. Now: React 19 + Vite 7, `openapi-typescript` generates
`api/schema.d.ts` and every frontend type derives from it, ESLint with `react-hooks` and
`jsx-a11y`, and Vitest for the pure logic. The codegen immediately surfaced real nullability the
hand-written types had hidden. Cost: a generation step that needs the API running, and one more
toolchain to keep current.

## ADR-15: AG Grid Community for the records table, client-side row model
600–5,000 records fit comfortably in memory, so the grid fetches once and handles sorting,
filtering, pagination and virtualisation locally: no datasource, no server-side model, and the
existing paged endpoint stays as-is. Community only; the enterprise package is not installed, so
row grouping, pivoting and integrated charts cannot creep in. Cost: ~300 KB of JavaScript, and
axe's best-practice focus-order rule fires on the grid's roving-tabindex DOM (see design-system.md).

## ADR-14: Operator-only information is absent from the API, not hidden by the UI
`/health` returns runtime, storage and enabled-service facts only when `DIAGNOSTICS` is on (local
by default). The UI gates its checkpoint panel and env-var hints on that flag. Cost: a deployed
operator reads CloudWatch for those facts instead of the page, which is where they belong.

## ADR-13: Live Comprehend price from the Price List API, no hard-coded fallback
The estimate shown before spending uses `pricing:GetProducts` filtered by region, cached 24 h in
the history database, served stale if a refresh fails, and reported as *unavailable* if nothing
was ever fetched. A hard-coded rate would eventually be wrong and look authoritative; an honest
"estimate unavailable, check AWS pricing" cannot mislead. Labelling itself is never blocked by a
missing price. Cost: one extra IAM action and a first-call latency of a few hundred ms.

## ADR-12: Labels as distant supervision plus a reviewed sample; checkpoints after every paid step
Task 2 needs labels. Comprehend labels everything for cents, a person reviews a random sample
(default 150) and the app reports agreement, so Task 2 can train on Comprehend labels and evaluate
on the reviewed subset. `label_source` and `comprehend_label` are stored per record so provenance is
never lost. Spending is estimated from the published rate, confirmed in a dialog, done in resumable
slices and checkpointed to CSV after each, so a crash cannot lose paid work. Cost: Comprehend's
biases become the training signal; the reviewed sample is what keeps that honest.

## ADR-11: Remove Django; SQLAlchemy for the two history tables
Running Django inside FastAPI worked but was poor architecture: two settings systems, a lazy-import
workaround so models could load, a migration runner at cold start, WhiteNoise for admin assets, and
a secret key to manage, all for an ORM and an admin screen. SQLAlchemy 2.0 gives typed models and a
session in under 150 lines, `create_all` replaces migrations for a tiny additive schema, and the
History dialog already shows the same data. Cost: no admin UI; schema changes beyond adding
nullable columns would need Alembic.

## ADR-10: A guided four-stage UI (Collect → Clean → Analyze → Export) with Tailwind
The first UI put inputs in a rail and results in tabs; people could not tell where to start. The
task has a natural order, so the UI is a stepper: search is the hero of stage one, cleaning and
normalisation are grouped in stage two, measurement is stage three, files are stage four. Stages
unlock as results exist and stay clickable afterwards. Tailwind v4 utilities reference theme
tokens so dark mode is a variable swap. Cost: less freedom to jump around; more explicit state in
`App.tsx`.

## ADR-9 (superseded by ADR-11): Django mounted inside FastAPI
Kept for the record: Django was mounted at `/admin` for its ORM and admin console.

## ADR-8: structlog for logging
Bound context (request id, dataset id) on every line without threading it through signatures;
JSON in Lambda, readable console locally; stdlib loggers routed through the same processors.
Cost: contributors must learn `log.info("event", key=value)` instead of f-strings.

## ADR-7: Spend ledger in the database, append-only
Replaces the S3 JSON counter, which could undercount under concurrent invocations. Every billed
read is a row; totals are sums. Cost: a database dependency for the X source.

## ADR-6: Secrets from SSM at startup, never CloudFormation parameters
Same pattern for the X token and the optional database URL. Swapping accounts is
one parameter change. Cost: a few hundred milliseconds on cold start; IAM must list exact ARNs.

## ADR-5: Client cancellation stops X paging at the next page boundary
`load_dataset` is async and watches `request.is_disconnected()`, setting a flag the sync fetch
polls. Cost: only effective while the connection is open (not through API Gateway after the
integration starts); the caps remain the hard limit.

## ADR-4: NLTK (WordNet + perceptron tagger), not spaCy
Roughly 300 MB smaller image, faster cold start, no model download step. POS tagging was added
after review found the noun-only path turned `was` into `wa`. Cost: WordNet quirks such as
`hated → hat`, documented in `rationale.yaml` rather than patched around.

## ADR-3: Comprehend is a fixed reference scorer, not the model
Task 1 is about preprocessing. Scoring the same records before and after with a fixed classifier
shows how much the steps move a real model. Task 2 swaps in the trained model without touching
anything else. Cost: a small Comprehend bill per run; cached by text hash.

## ADR-2: Only numbers go to Bedrock; record text never above DEBUG in logs
Posts are personal data. The explainer receives the `ImpactReport` with `sample_diffs` removed.

## ADR-1: Hugging Face via datasets-server REST, not the `datasets` library
Keeps the image small and fetches only the rows needed. Guaranteed path to the 500-record minimum.

## Deliberately not done
CloudFront in front of the site bucket (plain S3 website hosting suffices for a portfolio);
provisioned concurrency; drag-and-drop reordering (up/down buttons are keyboard-accessible);
client-side virtualised table (server paging at 50 rows is simpler and fast enough);
Postgres provisioning in the template (left to the operator so the stack has no always-on cost).
