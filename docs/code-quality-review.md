# Code quality review — September 24, 2026

This review covered the backend service boundaries, frontend workflow and async state, input
validation, persistence, paid-request accounting, export integrity, comments, documentation,
and existing regression and accessibility checks. The starting revision was `9714f56`.

The review used the [Broad Institute of MIT and Harvard coding and comment guide](https://mitcommlab.mit.edu/broad/commkit/coding-and-comment-style/)
as a readability reference, together with the repository's Ruff, mypy, TypeScript, ESLint, and
accessibility conventions. This is a project review, not an MIT certification.

## Findings addressed

| Finding | Impact | Resolution |
| --- | --- | --- |
| Backend tests inherited `backend/.env` and application environment settings. | A configured X token changed test expectations, and a configured bucket caused tests to attempt real S3 requests. The original run had nine configuration-related failures. | Test setup disables dotenv loading, clears inherited application settings, supplies test credentials, and creates separate temporary history/checkpoint locations for each session. A regression test supplies a developer-style `.env` and verifies that it cannot enable external services. |
| Manual review could open before the dataset summary loaded. | Entering a sample size while the total was still unknown could clamp the requested count to one post. | The manual-review entry button waits for the summary. A regression test holds the summary request open and verifies that review remains unavailable until the count loads. |
| Comments described behavior that had changed. | A maintainer could assume all records arrived in one HTTP request, that manual decisions were saved on a time interval, or that a test ledger was safe for concurrent callers. | Corrected pagination, manual-save, locking, async-route, startup, checkpoint, and collection-filter comments. Removed a dangling upload-limit comment. |
| Architecture documentation described an older deployment and storage model. | It showed a site without CloudFront, local in-memory working datasets, filtering after collection, and obsolete S3 save paths. | Replaced the diagrams and storage descriptions with the current CloudFront/private-S3 deployment, local JSON journal, filtering within pagination, and unique S3 export folders. |
| API generation instructions claimed Zod validators were generated. | An API change could leave runtime validation stale even after running the advertised command. | Corrected Make help and merged duplicate developer-guide sections: generate TypeScript declarations, then maintain the Zod validators and tests separately. |
| Preprocessing descriptions contradicted the unchecked negation option and overstated what counts as noise. | Readers could assume negation was preserved automatically or that removal could not discard sentiment. | Corrected Clean-screen wording, the shared step descriptions and catalogue fixture, and the cleaning/primer guides. The options and transformations are unchanged. |
| The browser audit assumed Chicago always uses UTC−05:00. | A winter audit would reject a correct UTC−06:00 download filename. | The filename check now accepts Chicago's standard and daylight offsets and matches the literal `.json` extension. |
| The repository overview lacked author attribution and current workflow images. | GitHub visitors could not clearly identify the author or see recent upload, review, and filename features. | Added Ron Harper to the README and package metadata, captured five application screens, and added two overview diagrams. |

The initial environment also lacked `psycopg`, despite it being a declared dependency. Installing
the declared driver resolved the tenth initial failure; no dependency constraint was changed.

Application changes in this pass are comments, docstrings, explanatory text, and the manual-review
loading guard. Provider calls, data transformations, and export behavior were not refactored.
Other executable changes cover test isolation and the browser audit's filename assertion.

## Review observations

- CSV preview and import use the same bounded server parser. Empty or malformed files and
  duplicate IDs are rejected or reported before import; the server validates again on import.
- Preprocessing returns new record representations. Exporters share `bundle_rows`, so manual
  labels and original/processed joins have one source of truth. Spreadsheet exports protect
  against formulas; Parquet preserves the data representation for later analysis.
- Paid collection saves cursor progress and uses an injected spend ledger. Existing tests cover
  deduplication before the retained target, rate limits, cancellation, resumed pages, concurrent
  edits, and conservative handling of ambiguous provider outcomes.
- Local persistence uses atomic replacement and per-dataset locks. S3 uses conditional writes.
  Checkpoint freshness is separate from working-bundle durability and is reported to the user.
- Frontend request ownership prevents obsolete responses from replacing newer state. Manual
  review serializes saves, preserves corrections, and waits for completion before reporting success.
- Shared preprocessing, scoring, export-row, validation, and request helpers reduce duplicated
  behavior. Type declarations generated from OpenAPI are an intentional build artifact.

## Remaining maintenance work

These items are not regressions introduced by this documentation pass:

1. **Frontend bundle size.** The production build reports an initial JavaScript chunk of about
   1.53 MB, or 438 kB compressed. `App.tsx` imports every stage eagerly, including the Analyze
   path that imports AG Grid. Consider loading stages on demand, then verify navigation,
   loading states, focus, and error handling. Splitting chunks without measuring the loading
   behavior would only hide the warning.
2. **Large orchestration functions.** `api/routes.py` contains roughly 940 lines and
   `XSearchSource.fetch` roughly 210. Some frontend components also compress multiple statements
   and JSX branches onto long lines. Future edits should extract cohesive responsibilities
   while preserving claim, billing, cursor, and persistence order. A broad formatting or
   structural rewrite was outside this pass.
3. **Dependency warnings.** The installed Starlette test client emits deprecation warnings
   involving httpx and AnyIO. Rollup also reports annotations in dependency code that it removes
   during the build. Check a coordinated dependency update separately; these warnings did not
   fail the tests or build.
4. **S3 export transactions.** A save writes three objects sequentially and writes the manifest
   last. A failed save can leave an incomplete folder; retrying uses a new folder. This is now
   documented. Consumers should require the manifest before treating an export as complete.

## Verification

| Check | Result |
| --- | --- |
| Ruff lint and formatting | Passed for backend source, tests, and tools. |
| mypy | Passed for 62 backend source files. |
| TypeScript and ESLint | Passed. |
| pytest | 477 passed; one optional PostgreSQL integration test skipped. |
| Vitest | 131 passed across 23 test files. |
| Vite production build | Passed; bundle-size and dependency-annotation warnings noted above. |
| Browser workflow | Passed CSV rejection/validation, local restore, preprocessing, diff navigation, manual-review persistence, and custom CSV download checks. |
| axe WCAG 2.0/2.1 A/AA | Zero reported violations across desktop light, desktop dark, and a 390 px phone layout, including fallback surfaces. |
| Keyboard checks | Passed upload activation, tab navigation, grid actions, Enter/Space activation, Escape dismissal, focus return, review navigation, and export controls. |
| Documentation rendering | Both README diagrams and all six architecture diagrams rendered; all five screenshot images loaded. |

Browser checks used a separate API and production preview with synthetic posts, temporary storage,
and fake sentiment responses. The user's running servers and saved datasets were not used.
The PostgreSQL concurrency test needs `TEST_POSTGRES_URL` and was not exercised against a live
server. These results do not establish live AWS/X access, full screen-reader compatibility, or
absence of every possible defect.
