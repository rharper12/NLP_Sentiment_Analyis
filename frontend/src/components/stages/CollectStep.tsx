import { useId, useState, type FormEvent } from "react";

import type { DatasetSummary } from "../../api/types";
import { api } from "../../api/client";
import { useAsync } from "../../hooks/useAsync";
import {
  DEFAULT_RANGE,
  TimeRangePicker,
  rangeError,
  toWindow,
  type TimeRange,
} from "./TimeRange";
import { Notice } from "../ui/Notice";
import { Skeleton } from "../ui/Skeleton";
import { CsvUpload } from "../collect/CsvUpload";
import { SavedDatasetPicker } from "../collect/SavedDatasetPicker";

type Source = "x" | "huggingface" | "csv" | "saved";
const MIN_RECORDS = 500;

interface Props {
  busy: boolean;
  error: Error | null;
  dataset: DatasetSummary | null;
  xConfigured: boolean;
  costPerRead?: number | null;
  onSearch: (query: string, limit: number, window: { start?: string; end?: string }) => void;
  onLoadSample: (limit: number) => void;
  onUpload: (file: File) => void;
  localDatasetsAvailable?: boolean;
  onRestore: (datasetId: string) => void;
  onResume?: () => void;
  onCancel: () => void;
  onContinue: () => void;
}

/**
 * Stage 1. The search box is the hero: type a topic, get posts. Sample dataset and CSV upload are
 * offered as secondary paths so the flow works without an X account.
 */
export function CollectStep({ busy, error, dataset, xConfigured, costPerRead, onSearch, onLoadSample, onUpload, onRestore, localDatasetsAvailable = false, onResume, onCancel, onContinue }: Props) {
  const download = useAsync<void>();
  const [source, setSource] = useState<Source>("x");
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(600);
  const [savedId, setSavedId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [range, setRange] = useState<TimeRange>(DEFAULT_RANGE);
  const ids = { q: useId(), n: useId(), tabs: useId() };

  const selectSource = (next: Source) => {
    if (source === next) return;
    setSource(next); setFile(null); setSavedId("");
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    if (source === "x") onSearch(query.trim(), limit, toWindow(range));
    else if (source === "huggingface") onLoadSample(limit);
    else if (source === "saved" && localDatasetsAvailable && savedId) onRestore(savedId);
    else if (source === "csv" && file) onUpload(file);
  };
  // A malformed custom range is caught here rather than by the server, so the person is not made
  // to wait for a round trip to learn the dates are the wrong way round.
  const canSubmit =
    !busy &&
    (source === "x"
      ? query.trim().length > 0 && !rangeError(range)
      : source === "huggingface" || (source === "saved" ? localDatasetsAvailable && !!savedId : !!file));

  const sources: [Source, string][] = [["x", "Search X"], ["huggingface", "Sample dataset"], ["csv", "Upload CSV"]];
  if (localDatasetsAvailable) sources.push(["saved", "Saved datasets"]);

  return (
    <section className="mx-auto flex max-w-3xl flex-col gap-8">
      <div className="pt-4 text-center sm:pt-10">
        <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">Collect your dataset</h2>
        <p className="mt-2 text-muted">Search posts on X, try the sample dataset, or import your own CSV.</p>
      </div>

      <form onSubmit={submit} className="glass-panel flex flex-col gap-5 p-4 sm:p-6">
        <div role="tablist" aria-label="Data source" className={`grid grid-cols-2 gap-2 text-sm ${localDatasetsAvailable ? "sm:grid-cols-4" : "sm:grid-cols-3"}`}>
          {sources.map(([id, label]) => (
            <button key={id} type="button" role="tab" id={`${ids.tabs}-${id}`} aria-controls={`${ids.tabs}-panel`} tabIndex={source === id ? 0 : -1} disabled={busy} aria-selected={source === id} onClick={() => selectSource(id)}
              onKeyDown={(event) => {
                const index = sources.findIndex(([key]) => key === id);
                const target = event.key === "ArrowRight" ? (index + 1) % sources.length : event.key === "ArrowLeft" ? (index - 1 + sources.length) % sources.length : event.key === "Home" ? 0 : event.key === "End" ? sources.length - 1 : null;
                if (target == null) return;
                event.preventDefault();
                const next = sources[target][0];
                selectSource(next);
                document.getElementById(`${ids.tabs}-${next}`)?.focus();
              }}
              className="selectable px-3 py-2 font-medium">
              {label}
            </button>
          ))}
        </div>

        <div id={`${ids.tabs}-panel`} role="tabpanel" aria-labelledby={`${ids.tabs}-${source}`} className="flex flex-col gap-5">
        {source === "x" && (
          <div className="flex flex-col gap-2">
            <label htmlFor={ids.q} className="text-sm text-muted">Topic</label>
            <input id={ids.q} className="field text-lg" value={query} onChange={(e) => setQuery(e.target.value)}
              placeholder='e.g. "lindsay clancy" trial   or   #WWDC -has:links' autoComplete="off" />
            <p className="text-xs text-muted">Choose recent posts or custom historical dates. <code className="rounded bg-surface-2 px-1">lang:en -is:retweet</code> is added unless you set <code className="rounded bg-surface-2 px-1">lang:</code> yourself. X search operators are supported.</p>
            <a href="https://docs.x.com/x-api/posts/search/integrate/build-a-query" target="_blank" rel="noopener noreferrer" className="self-start text-sm underline underline-offset-4">X query guide: operators and examples <span className="text-xs">(opens in a new tab)</span></a>
            <TimeRangePicker value={range} onChange={setRange} />
            {!xConfigured && <p className="text-xs text-warn-ink">No X token is configured on the server, so this search will be refused. Use the sample dataset to explore.</p>}
          </div>
        )}
        {source === "huggingface" && (
          <p className="text-sm text-muted">Labelled tweets from <code className="rounded bg-surface-2 px-1">cardiffnlp/tweet_eval</code> (sentiment). No credentials, no cost. Good for learning the pipeline before spending X credits.</p>
        )}
        {source === "csv" && <CsvUpload disabled={busy} onValidated={setFile} />}
        {source === "saved" && localDatasetsAvailable && <SavedDatasetPicker disabled={busy} selected={savedId} onSelect={setSavedId} />}

        {(source === "x" || source === "huggingface") && (
          <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:gap-6">
            <div className="flex flex-col gap-1">
              <label htmlFor={ids.n} className="text-sm text-muted">How many posts</label>
              <input id={ids.n} type="number" min={MIN_RECORDS} max={5000} step={50} value={limit} onChange={(e) => setLimit(Number(e.target.value))} className="field tnum w-36" />
            </div>
            <p className="text-xs text-muted sm:pb-2.5">
              {source === "x" ? <>Preflight estimate: <strong className="tnum text-ink">{costPerRead == null ? "unavailable" : `$${(limit * costPerRead).toFixed(2)}`}</strong> for {limit.toLocaleString()} reads. Filtering can require more billed reads than retained posts.</> : "The assignment needs at least 500."}
            </p>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-3">
          <button type="submit" className="btn-primary min-w-40" disabled={!canSubmit}>
            {busy ? "Loading…" : source === "x" ? "Search and collect" : source === "huggingface" ? "Load sample" : source === "saved" ? "Open in Clean →" : "Import CSV"}
          </button>
          {busy && <button type="button" className="btn" onClick={onCancel}>Cancel</button>}
          {busy && source === "x" && <span className="text-xs text-muted">Cancelling stops at the next page, so at most 100 more posts are billed.</span>}
        </div>
        </div>
      </form>

      <Notice error={error} warnings={dataset?.warnings} />
      <Notice error={download.error} />
      {onResume && !busy && <div className="glass-panel flex flex-col gap-2 p-4">
        <p className="text-sm">Collection can continue from saved progress. A timed-out provider response may still have incurred a charge.</p>
        {dataset?.retry_at && <p className="text-sm">Retry after {new Date(dataset.retry_at * 1000).toLocaleTimeString()}.</p>}
        <button type="button" className="btn" onClick={onResume}>Resume collection</button>
      </div>}

      {busy && (
        <div className="glass-panel flex flex-col gap-3 p-4 sm:p-6" aria-busy="true">
          <Skeleton className="w-48" />
          {Array.from({ length: 4 }, (_, i) => <Skeleton key={i} className={i % 2 ? "w-5/6" : "w-full"} />)}
        </div>
      )}

      {dataset && !busy && (
        <div className="glass-panel flex flex-col gap-4 p-4 sm:p-6">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h3 className="text-lg font-semibold"><span className="tnum">{dataset.record_count.toLocaleString()}</span> posts collected</h3>
            <span className="text-xs text-muted">
              from {dataset.source_type === "x" ? "X" : dataset.source_type === "huggingface" ? "Hugging Face" : "your CSV"}
              {dataset.billed_reads != null && <> · {dataset.billed_reads.toLocaleString()} billed reads</>}
              {dataset.committed_cost_usd != null && <> · committed spend ${dataset.committed_cost_usd.toFixed(2)}</>}
            </span>
          </div>
          {dataset.query && <p className="tnum truncate text-sm text-muted">Query: {dataset.query}</p>}
          {dataset.window_start && (
            <p className="tnum text-sm text-muted">
              Window: {new Date(dataset.window_start).toLocaleString()} to{" "}
              {dataset.window_end ? new Date(dataset.window_end).toLocaleString() : "now"}
            </p>
          )}
          {dataset.truncated_reason && <p className="text-sm text-muted">Stopped early: {dataset.truncated_reason}.</p>}
          <ul className="divide-y divide-rule text-sm">
            {dataset.preview.slice(0, 6).map((r) => (
              <li key={r.id} className="flex gap-3 py-2">
                <span className="flex-1 leading-snug">{r.text}</span>
                {r.label && <span className="tnum shrink-0 text-xs text-muted">{r.label}</span>}
              </li>
            ))}
          </ul>
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-rule pt-4">
            <p className="text-sm text-muted">Next: decide how to clean and normalise this text.</p>
            <button type="button" className="btn" disabled={download.loading} onClick={() => void download.run((signal) => api.download(dataset.dataset_id, "original.json", signal))}>{download.loading ? "Downloading…" : "Download original dataset"}</button>
            <button type="button" className="btn-primary" onClick={onContinue}>Continue to Clean →</button>
          </div>
        </div>
      )}
    </section>
  );
}
