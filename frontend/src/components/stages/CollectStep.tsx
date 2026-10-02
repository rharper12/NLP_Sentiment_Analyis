import { useEffect, useId, useRef, useState, type FormEvent } from "react";

import type { CollectionWindow, DatasetSummary } from "../../api/types";
import { api } from "../../api/client";
import { useAsync } from "../../hooks/useAsync";
import { DEFAULT_RANGE, TimeRangePicker, rangeError, toWindow, type TimeRange } from "./TimeRange";
import { Notice } from "../ui/Notice";
import { Skeleton } from "../ui/Skeleton";
import { CsvUpload } from "../collect/CsvUpload";
import { SavedDatasetPicker } from "../collect/SavedDatasetPicker";
import { AdditionalCandidates } from "../collect/AdditionalCandidates";
import { CollectionStatus } from "../collect/CollectionStatus";

type Source = "x" | "huggingface" | "csv" | "saved";
const MIN_RECORDS = 500;

interface Props {
  busy: boolean;
  error: Error | null;
  dataset: DatasetSummary | null;
  collectionMessage?: string;
  xConfigured: boolean;
  costPerRead?: number | null;
  onSearch: (query: string, limit: number, window: CollectionWindow) => void;
  onModeChange?: (consumer: boolean) => void;
  onAdditional?: (target: number) => void;
  onLoadSample: (limit: number) => void;
  onUpload: (file: File) => void;
  localDatasetsAvailable?: boolean;
  onRestore: (datasetId: string) => void;
  onResume?: () => void;
  onCancel: () => void;
  onContinue: () => void;
}

/**
 * Collect first, then show saved totals and the next action. New-search controls collapse
 * after success so they cannot be mistaken for adding to the current dataset.
 */
export function CollectStep({
  busy,
  error,
  dataset,
  collectionMessage,
  xConfigured,
  costPerRead,
  onSearch,
  onModeChange,
  onAdditional,
  onLoadSample,
  onUpload,
  onRestore,
  localDatasetsAvailable = false,
  onResume,
  onCancel,
  onContinue,
}: Props) {
  const download = useAsync<void>();
  const [source, setSource] = useState<Source>("x");
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(600);
  const [savedId, setSavedId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [range, setRange] = useState<TimeRange>(DEFAULT_RANGE);
  const [consumer, setConsumer] = useState(false);
  const [authorLimit, setAuthorLimit] = useState(2);
  const [reviewedTarget, setReviewedTarget] = useState(500);
  const selectCommit = useRef(false);
  const resultsHeading = useRef<HTMLHeadingElement>(null);
  const previousId = useRef(dataset?.dataset_id);
  const wasBusy = useRef(busy);
  useEffect(() => {
    if (!busy && (wasBusy.current || dataset?.dataset_id !== previousId.current)) {
      previousId.current = dataset?.dataset_id;
      if (dataset) resultsHeading.current?.focus();
    }
    wasBusy.current = busy;
  }, [busy, dataset]);
  const ids = { q: useId(), n: useId(), tabs: useId(), modeHelp: useId() };

  const selectSource = (next: Source) => {
    if (source === next) return;
    setSource(next);
    onModeChange?.(next === "x" && consumer);
    setFile(null);
    setSavedId("");
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    // Native selects can submit on Enter; choosing an option does not authorize a search.
    if (selectCommit.current) return;
    if (!canSubmit) return;
    if (source === "x")
      onSearch(query.trim(), limit, {
        ...toWindow(range),
        ...(consumer
          ? {
              preset: "consumer_reactions" as const,
              per_author_limit: authorLimit,
              reviewed_target: reviewedTarget,
            }
          : {}),
      });
    else if (source === "huggingface") onLoadSample(limit);
    else if (source === "saved" && localDatasetsAvailable && savedId) onRestore(savedId);
    else if (source === "csv" && file) onUpload(file);
  };
  // A malformed custom range is caught here rather than by the server, so the person is not made
  // to wait for a round trip to learn the dates are the wrong way round.
  const days = (Date.parse(range.to) - Date.parse(range.from)) / 86_400_000 + 1;
  const consumerError =
    consumer && source === "x"
      ? days > 31
        ? "Choose at most 31 calendar days."
        : limit < days * 10
          ? "Allow at least 10 candidates per requested day."
          : null
      : null;
  const canSubmit =
    !busy &&
    (source === "x"
      ? query.trim().length > 0 &&
        !rangeError(range) &&
        !consumerError &&
        Number.isInteger(limit) &&
        limit > 0 &&
        limit <= 5000 &&
        (!consumer ||
          (Number.isInteger(authorLimit) &&
            authorLimit >= 1 &&
            authorLimit <= 100 &&
            Number.isInteger(reviewedTarget) &&
            reviewedTarget >= 1 &&
            reviewedTarget <= 5000))
      : source === "huggingface" ||
        (source === "saved" ? localDatasetsAvailable && !!savedId : !!file));

  const sources: [Source, string][] = [
    ["x", "Search X"],
    ["huggingface", "Sample dataset"],
    ["csv", "Upload CSV"],
  ];
  if (localDatasetsAvailable) sources.push(["saved", "Saved datasets"]);

  const form = (
    <form
      onSubmit={submit}
      onKeyDownCapture={(event) => {
        selectCommit.current = event.key === "Enter" && (event.target as HTMLElement).tagName === "SELECT";
      }}
      onKeyUpCapture={() => { selectCommit.current = false; }}
      onPointerDownCapture={() => { selectCommit.current = false; }}
      className="glass-panel flex flex-col gap-5 p-4 sm:p-6"
    >
      <div
        role="tablist"
        aria-label="Data source"
        className={`grid grid-cols-2 gap-2 text-sm ${localDatasetsAvailable ? "sm:grid-cols-4" : "sm:grid-cols-3"}`}
      >
        {sources.map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            id={`${ids.tabs}-${id}`}
            aria-controls={`${ids.tabs}-panel`}
            tabIndex={source === id ? 0 : -1}
            disabled={busy}
            aria-selected={source === id}
            onClick={() => selectSource(id)}
            onKeyDown={(event) => {
              const index = sources.findIndex(([key]) => key === id);
              const target =
                event.key === "ArrowRight"
                  ? (index + 1) % sources.length
                  : event.key === "ArrowLeft"
                    ? (index - 1 + sources.length) % sources.length
                    : event.key === "Home"
                      ? 0
                      : event.key === "End"
                        ? sources.length - 1
                        : null;
              if (target == null) return;
              event.preventDefault();
              const next = sources[target][0];
              selectSource(next);
              document.getElementById(`${ids.tabs}-${next}`)?.focus();
            }}
            className="selectable px-3 py-2 font-medium"
          >
            {label}
          </button>
        ))}
      </div>

      <div
        id={`${ids.tabs}-panel`}
        role="tabpanel"
        aria-labelledby={`${ids.tabs}-${source}`}
        className="flex flex-col gap-5"
      >
        {source === "x" && (
          <div className="flex flex-col gap-2">
            <label className="flex flex-col gap-1 text-sm">
              Collection option
              <select
                className="field"
                aria-describedby={ids.modeHelp}
                value={consumer ? "consumer" : "general"}
                onChange={(e) => {
                  const selected = e.target.value === "consumer";
                  setConsumer(selected);
                  onModeChange?.(selected);
                  if (selected)
                    setRange((current) => ({
                      ...current,
                      preset: "custom",
                      timezone:
                        current.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone,
                    }));
                }}
              >
                <option value="general">General search — sentiment labeling</option>
                <option value="consumer">Consumer reactions — include/exclude + sentiment</option>
              </select>
            </label>
            <p id={ids.modeHelp} className="text-sm text-muted">
              {consumer ? "Keep or exclude posts and label sentiment in one review, then clean the text." : "Collect matching posts, clean the text, then label sentiment."}
            </p>
            <label htmlFor={ids.q} className="text-sm text-muted">
              Topic
            </label>
            <input
              id={ids.q}
              className="field text-lg"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder='e.g. "coffee maker" OR #CoffeeMaker'
              autoComplete="off"
            />
            <details className="text-sm text-muted">
              <summary className="cursor-pointer">Search tips</summary>
              <p className="mt-2">English posts and no reposts are the defaults. X search operators are supported.</p>
              <a href="https://docs.x.com/x-api/posts/search/integrate/build-a-query" target="_blank" rel="noopener noreferrer" className="underline underline-offset-4">X query guide (opens in a new tab)</a>
            </details>
            <TimeRangePicker value={range} onChange={setRange} calendarOnly={consumer} />
            {consumerError && (
              <p className="text-xs text-warn-ink" role="alert">
                {consumerError}
              </p>
            )}
            {consumer && (
              <>
                <div className="flex flex-wrap gap-4">
                  <label className="flex flex-col gap-1 text-sm">
                    Final reviewed target
                    <input
                      className="field w-36"
                      type="number"
                      min={1}
                      max={5000}
                      value={reviewedTarget}
                      onChange={(e) => setReviewedTarget(Number(e.target.value))}
                    />
                  </label>
                  <label className="flex flex-col gap-1 text-sm">
                    Included posts per known author
                    <input
                      className="field w-36"
                      type="number"
                      min={1}
                      max={100}
                      value={authorLimit}
                      onChange={(e) => setAuthorLimit(Number(e.target.value))}
                    />
                  </label>
                </div>
                <details className="text-sm text-muted">
                  <summary className="cursor-pointer">How this sample is collected</summary>
                  <p className="mt-2">The collection goal is split across days from start to end. X returns newest posts first within each day. Empty days can leave a shortfall; page minimums can add extra posts.</p>
                  <p className="mt-2">All sentiments are eligible. Screening suggestions need your confirmation. The author limit controls sampling, not bot detection. Keyword search can miss replies that do not name the topic.</p>
                </details>
              </>
            )}
            {!xConfigured && (
              <p className="text-xs text-warn-ink">
                No X token is configured on the server, so this search will be refused. Use the
                sample dataset to explore.
              </p>
            )}
          </div>
        )}
        {source === "huggingface" && (
          <p className="text-sm text-muted">
            Labelled tweets from{" "}
            <code className="rounded bg-surface-2 px-1">cardiffnlp/tweet_eval</code> (sentiment).
            No credentials, no cost. Good for learning the pipeline before spending X credits.
          </p>
        )}
        {source === "csv" && <CsvUpload disabled={busy} onValidated={setFile} />}
        {source === "saved" && localDatasetsAvailable && (
          <SavedDatasetPicker disabled={busy} selected={savedId} onSelect={setSavedId} />
        )}

        {(source === "x" || source === "huggingface") && (
          <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:gap-6">
            <div className="flex flex-col gap-1">
              <label htmlFor={ids.n} className="text-sm text-muted">
                {consumer && source === "x"
                  ? "Posts to collect"
                  : "How many posts"}
              </label>
              <input
                id={ids.n}
                type="number"
                min={consumer && source === "x" ? 10 : MIN_RECORDS}
                max={5000}
                step={consumer && source === "x" ? 1 : 50}
                value={limit}
                onChange={(e) => setLimit(Number(e.target.value))}
                className="field tnum w-36"
              />
            </div>
            <p className="text-xs text-muted sm:pb-2.5">
              {source === "x" ? (
                <>
                  Paid X request · Estimated cost:{" "}
                  <strong className="tnum text-ink">
                    {costPerRead == null ? "unavailable" : `$${(limit * costPerRead).toFixed(2)}`}
                  </strong>. Actual cost may vary; spend caps apply.
                </>
              ) : (
                "The assignment needs at least 500."
              )}
            </p>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-3">
          <button type="submit" className="btn-primary min-w-40" disabled={!canSubmit}>
            {busy
              ? "Loading…"
              : source === "x"
                ? "Search and collect"
                : source === "huggingface"
                  ? "Load sample"
                  : source === "saved"
                    ? "Open dataset →"
                    : "Import CSV"}
          </button>
          {busy && (
            <button type="button" className="btn" onClick={onCancel}>
              Cancel
            </button>
          )}
          {busy && source === "x" && (
            <span className="text-xs text-muted">
              Cancelling stops at the next page, so at most 100 more posts are billed.
            </span>
          )}
        </div>
      </div>
    </form>
  );

  return (
    <section className="mx-auto flex max-w-3xl flex-col gap-6">
      <div className="pt-4 text-center sm:pt-10">
        <h2 ref={resultsHeading} tabIndex={-1} className="text-2xl font-semibold tracking-tight sm:text-3xl">
          Collect your dataset
        </h2>
        <p className="mt-2 text-muted">
          {dataset ? "Your progress across all requests." : "Choose a topic and a collection goal."}
        </p>
      </div>
      <Notice error={error} warnings={dataset?.warnings} />
      <Notice error={download.error} />
      {dataset && (
        <CollectionStatus dataset={dataset} busy={busy} message={collectionMessage}>
          {dataset.consumer_policy && onAdditional && (
            <AdditionalCandidates
              dataset={dataset}
              stage="collect"
              busy={busy}
              costPerRead={costPerRead}
              onRequest={onAdditional}
            />
          )}
          {!dataset.consumer_policy && onResume && (
            <button type="button" className="btn self-start" disabled={busy} onClick={onResume}>
              Get more posts
            </button>
          )}
          <div className="flex flex-wrap items-center gap-3 border-t border-rule pt-4">
            <button
              type="button"
              className="btn-primary"
              disabled={busy || dataset.record_count === 0}
              onClick={onContinue}
            >
              {dataset.consumer_policy ? "Continue to Review & label →" : "Continue to Clean →"}
            </button>
            <button
              type="button"
              className="btn"
              disabled={busy || download.loading}
              onClick={() => void download.run((signal) =>
                api.download(dataset.dataset_id, "original.json", signal),
              )}
            >
              {download.loading ? "Downloading…" : "Download original dataset"}
            </button>
            {busy && <button type="button" className="btn" onClick={onCancel}>Cancel</button>}
          </div>
        </CollectionStatus>
      )}
      {busy && !dataset && (
        <div className="glass-panel flex flex-col gap-3 p-4 sm:p-6" aria-busy="true">
          <p role="status">Collecting posts…</p>
          <Skeleton className="w-48" />
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} className={i % 2 ? "w-5/6" : "w-full"} />
          ))}
        </div>
      )}
      {!dataset && onResume && !busy && (
        <button type="button" className="btn self-start" onClick={onResume}>Get more posts</button>
      )}
      {dataset ? (
        <details key={dataset.dataset_id} className="rounded-xl border border-rule p-4">
          <summary className="cursor-pointer font-medium">Start another collection</summary>
          <p className="my-3 text-sm text-muted">
            A different search creates a separate dataset and a new paid request.
          </p>
          {form}
        </details>
      ) : form}
    </section>
  );
}
