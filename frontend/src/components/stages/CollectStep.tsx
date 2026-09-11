import { useId, useState, type FormEvent } from "react";

import type { DatasetSummary } from "../../api/types";
import { Notice } from "../ui/Notice";
import { Skeleton } from "../ui/Skeleton";

type Source = "x" | "huggingface" | "csv";
const COST_PER_READ = 0.005;
const MIN_RECORDS = 500;

interface Props {
  busy: boolean;
  error: Error | null;
  dataset: DatasetSummary | null;
  xConfigured: boolean;
  onSearch: (query: string, limit: number) => void;
  onLoadSample: (limit: number) => void;
  onUpload: (file: File) => void;
  onCancel: () => void;
  onContinue: () => void;
}

/**
 * Stage 1. The search box is the hero: type a topic, get posts. Sample dataset and CSV upload are
 * offered as secondary paths so the flow works without an X account.
 */
export function CollectStep({ busy, error, dataset, xConfigured, onSearch, onLoadSample, onUpload, onCancel, onContinue }: Props) {
  const [source, setSource] = useState<Source>("x");
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(600);
  const [file, setFile] = useState<File | null>(null);
  const ids = { q: useId(), n: useId(), f: useId() };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (source === "x") onSearch(query.trim(), limit);
    else if (source === "huggingface") onLoadSample(limit);
    else if (file) onUpload(file);
  };
  const canSubmit = !busy && (source === "x" ? query.trim().length > 0 : source === "huggingface" || !!file);

  return (
    <section className="mx-auto flex max-w-3xl flex-col gap-8">
      <div className="pt-4 text-center sm:pt-10">
        <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">What do people think about…</h2>
        <p className="mt-2 text-muted">Search a topic on X and collect recent posts as your dataset. Anything works: a trial, a product launch, a hashtag.</p>
      </div>

      <form onSubmit={submit} className="glass-panel flex flex-col gap-5 p-4 sm:p-6">
        <div role="tablist" aria-label="Data source" className="grid grid-cols-3 gap-2 text-sm">
          {([["x", "Search X"], ["huggingface", "Sample dataset"], ["csv", "Upload CSV"]] as [Source, string][]).map(([id, label]) => (
            <button key={id} type="button" role="tab" aria-selected={source === id} onClick={() => setSource(id)}
              className="selectable px-3 py-2 font-medium">
              {label}
            </button>
          ))}
        </div>

        {source === "x" && (
          <div className="flex flex-col gap-2">
            <label htmlFor={ids.q} className="text-sm text-muted">Topic</label>
            <input id={ids.q} className="field text-lg" value={query} onChange={(e) => setQuery(e.target.value)} autoFocus
              placeholder='e.g. "lindsay clancy" trial   or   #WWDC -has:links' autoComplete="off" />
            <p className="text-xs text-muted">Posts from the last 7 days. <code className="rounded bg-surface-2 px-1">lang:en -is:retweet</code> is added unless you set <code className="rounded bg-surface-2 px-1">lang:</code> yourself. Any X search operator works.</p>
            {!xConfigured && <p className="text-xs text-warn-ink">No X token is configured on the server, so this search will be refused. Use the sample dataset to explore.</p>}
          </div>
        )}
        {source === "huggingface" && (
          <p className="text-sm text-muted">Labelled tweets from <code className="rounded bg-surface-2 px-1">cardiffnlp/tweet_eval</code> (sentiment). No credentials, no cost. Good for learning the pipeline before spending X credits.</p>
        )}
        {source === "csv" && (
          <div className="flex flex-col gap-2">
            <label htmlFor={ids.f} className="text-sm text-muted">CSV file</label>
            <input id={ids.f} type="file" accept=".csv,text/csv" className="text-sm text-muted" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            <p className="text-xs text-muted">Needs a <code className="rounded bg-surface-2 px-1">text</code> column; <code className="rounded bg-surface-2 px-1">label</code> and <code className="rounded bg-surface-2 px-1">id</code> are optional.</p>
          </div>
        )}

        {source !== "csv" && (
          <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:gap-6">
            <div className="flex flex-col gap-1">
              <label htmlFor={ids.n} className="text-sm text-muted">How many posts</label>
              <input id={ids.n} type="number" min={MIN_RECORDS} max={5000} step={50} value={limit} onChange={(e) => setLimit(Number(e.target.value))} className="field tnum w-36" />
            </div>
            <p className="text-xs text-muted sm:pb-2.5">
              {source === "x" ? <>About <strong className="tnum text-ink">${(limit * COST_PER_READ).toFixed(2)}</strong> in X read credits. Fewer may return if the topic is quiet this week.</> : "The assignment needs at least 500."}
            </p>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-3">
          <button type="submit" className="btn-primary min-w-40" disabled={!canSubmit}>
            {busy ? "Collecting…" : source === "x" ? "Search and collect" : source === "huggingface" ? "Load sample" : "Upload"}
          </button>
          {busy && <button type="button" className="btn" onClick={onCancel}>Cancel</button>}
          {busy && source === "x" && <span className="text-xs text-muted">Cancelling stops at the next page, so at most 100 more posts are billed.</span>}
        </div>
      </form>

      <Notice error={error} warnings={dataset?.warnings} />

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
              {dataset.estimated_cost_usd != null && <> · spent ${dataset.estimated_cost_usd.toFixed(2)}</>}
            </span>
          </div>
          {dataset.query && <p className="tnum truncate text-sm text-muted">Query: {dataset.query}</p>}
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
            <button type="button" className="btn-primary" onClick={onContinue}>Continue to Clean →</button>
          </div>
        </div>
      )}
    </section>
  );
}
