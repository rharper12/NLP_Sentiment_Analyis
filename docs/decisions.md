# Decisions

Architecture decision records, newest first. Each says what was decided, why, and what it costs.

## ADR-23: A search window that clamps rather than refuses
X's recent search accepts `start_time` and `end_time` inside a seven-day horizon, so the UI offers
presets and a custom date pair. Bounds outside the horizon are clamped, not rejected: the "last 7
days" preset computes `now - 168h` in the browser and the server checks it milliseconds later, so a
strict comparison would fail the most common path on clock skew alone. A range that runs backwards
is a different thing — no clamping repairs it, and sending it draws a 400 from X that our error
mapping would misattribute to the query syntax — so that is refused with a message naming the
problem. The window is recorded on the dataset and printed in the report, because "posts about the
launch" means nothing without the days it covers.

## ADR-22: Deduplicate at collection, with a prefix filter and a deliberately high threshold
Duplicates that straddle a train/test split let a model score itself on memorised text, which on a
600-row corpus can move accuracy several points. X is full of copypasta and quote-tweets that
differ only in a link, so posts are deduplicated at collection: exact matching after normalising
away case, punctuation, links and mentions, then near-duplicate matching by Jaccard overlap.

Two choices worth recording. The threshold is 0.9, which is high on purpose: a one-word difference
in a short post scores about 0.85, and "this verdict is good" versus "this verdict is not good"
scores 0.75 — collapsing those would silently delete the negative half of a corpus. And candidates
come from a prefix filter (the rarest `floor((1-t)*size)+1` tokens), which is exact rather than
heuristic. The first implementation indexed every token below a frequency cutoff and degenerated
to comparing all pairs on a small-vocabulary corpus: 15.3 s for 5,000 posts, against 1.7 s now.

## ADR-21: Filter contentless posts at collection; sweep emptied ones at the end
Posts do not arrive empty, they become empty, so a missing-data check at the head of the pipeline
caught almost nothing on X data. Two changes: the X adapter now measures length in *content*
tokens (links and mentions removed) and drops posts with fewer than five, recording a per-reason
tally on the dataset; and `missing_data` moved to the end of the default order, where the steps
that empty a record have already run. Filtering at collection rather than mid-pipeline is the
important half: it fixes N across every preprocessing configuration, so an agreement figure for
"with stopword removal" versus "without" is computed over the same rows. The tally is surfaced in
the UI and the report, because a dropped-post count is a limitation a write-up should state rather
than a number to hide. Cost: one more field on `Dataset`, and a UI group that exists to hold a
single step.

## ADR-20: Repository split into backend/ and frontend/, with Zod at the client boundary
The Python package used to own the repository root, which made the frontend look like an
afterthought and forced every tool to disambiguate paths. Each half now owns its folder, its
dependency manifest and its tests, and the root `Makefile` is the only thing that knows about
both. Alongside it, every API response is now parsed by a Zod schema bound to the generated
OpenAPI type: the types say what the backend claims, the schemas check what it sent. Objects are
loose so a newer API cannot break an older UI, and a mismatch raises `ApiContractError` naming the
field. Cost: ~200 lines of schema to keep in step, enforced by the compiler rather than by memory.

## ADR-19: Operator sessions for browsers; direct API keys for scripts
The API spends money on someone's behalf: an X search bills the operator's credits, Comprehend
labelling bills their AWS account. An unauthenticated internet-facing URL is therefore an open
wallet, which no amount of spend-capping fixes. The operator enters the permanent API key through
the sign-in form, which exchanges it at `POST /auth/session` for a one-hour bearer session.
The session token is held only in browser memory and sent in the `Authorization` header;
reloading requires sign-in again. The permanent key is not embedded in Vite output, JavaScript
bundles or public S3 assets, persisted in browser storage, or placed in query strings.
This session flow replaces the earlier decision to embed the permanent key in the browser build.

Scripts and direct operator tooling may separately send the permanent key in `X-API-Key`.
Configured authentication protects application routes; `/health` and the session exchange are
public, and the exchange validates the operator key using a constant-time comparison.
Authentication is optional locally, but Lambda fails closed without `API_KEY` or a resolved
`API_KEY_SSM_PATH`. `/health` reports `auth_required`. Cost: one more secret to manage and periodic
browser sign-in; this shared operator identity gates the budget rather than authenticating
individual users. A multi-user deployment still needs a proper identity provider.

## ADR-18: React 19 APIs adopted only where they fit
The upgrade to React 19 made `useActionState`, `useOptimistic`, `use`, `<Context>` as a provider
and document metadata available. Only **document metadata** was adopted (`DocumentTitle` renders
`<title>`, which React hoists): it satisfies WCAG 2.4.2 for a single-page app whose title would
otherwise never change. The others were considered and rejected on merit, not overlooked:
`useOptimistic` does not fit the reviewer, because a label is authoritative locally and a failed
save is **retried**, not reverted — showing a revert would misrepresent what the app does;
`useActionState` would replace the collect form's pending state but would fight the cancel button,
which needs an `AbortController` the action API does not surface; `forwardRef` and legacy context
were never used, so `ref`-as-prop and `<Context>` have nothing to replace. Adopting an API to be
able to say you use it is how a codebase acquires patterns nobody can justify later.

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
the history database, served stale for at most an additional 48 h if refresh fails, and
reported as *unavailable* when absent, expired, invalid, or future-dated. Both cache TTL and
stale grace are nonnegative configurable durations. A hard-coded rate would eventually be wrong and look authoritative; an honest
"estimate unavailable, check AWS pricing" cannot mislead. Labelling itself is never blocked by a
missing price. Cost: one extra IAM action and a first-call latency of a few hundred ms.

## ADR-12: Labels as distant supervision plus a reviewed sample; checkpoints after every paid step
Task 2 needs labels. Comprehend labels everything for cents, a person reviews a random sample
(default 150) and the app reports agreement, so Task 2 can train on Comprehend labels and evaluate
on the reviewed subset. `label_source` and `comprehend_label` are stored per record so provenance is
never lost. Spending is estimated from the published rate, confirmed in a dialog, done in resumable
slices with incremental durable progress and CSV snapshots. Provider success immediately before
persistence remains ambiguous; checkpoint replacement failures retain older files with explicit status. Cost: Comprehend's
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
