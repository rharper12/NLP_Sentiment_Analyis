import type { ReactNode } from "react";

import type { DatasetSummary } from "../../api/types";
import { Skeleton } from "../ui/Skeleton";

/** Collection counts stay separate from human review decisions and provider billing. */
export function CollectionStatus({ dataset, busy = false, message, children }: {
  dataset: DatasetSummary;
  busy?: boolean;
  message?: string;
  children?: ReactNode;
}) {
  const target = dataset.candidate_target;
  const remaining = target == null ? null : Math.max(0, target - dataset.record_count);
  const policy = dataset.consumer_policy;
  const firstBatch = dataset.first_batch_saved;
  const lastBatch = dataset.last_batch_saved;
  return (
    <section className="glass-panel flex flex-col gap-5 p-4 sm:p-6" aria-label="Saved collection">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-lg font-semibold">Collection results</h3>
        <span className="rounded-full bg-surface-2 px-3 py-1 text-xs font-medium">
          {busy ? "Collecting…" : dataset.partial ? "Paused" : remaining ? "Search finished" : "Ready"}
        </span>
      </div>
      <dl className={dataset.source_type === "x" ? "grid grid-cols-3 gap-3" : "grid gap-3"}>
        <div>
          <dt className="min-h-10 text-sm text-muted sm:min-h-0">Total saved</dt>
          <dd className="tnum mt-1 text-2xl font-semibold sm:text-3xl">{dataset.record_count.toLocaleString()}</dd>
        </div>
        {dataset.source_type === "x" && <>
        <div>
          <dt className="min-h-10 text-sm text-muted sm:min-h-0">Added last request</dt>
          <dd className="tnum mt-1 text-2xl font-semibold sm:text-3xl">
            {busy ? <Skeleton className="mt-2 w-16" /> : lastBatch?.toLocaleString() ?? <>
              <span aria-hidden="true">—</span><span className="sr-only">Not recorded</span>
            </>}
          </dd>
        </div>
        <div>
          <dt className="min-h-10 text-sm text-muted sm:min-h-0">Still to collect</dt>
          <dd className="tnum mt-1 text-2xl font-semibold sm:text-3xl">
            {remaining?.toLocaleString() ?? <>
              <span aria-hidden="true">—</span><span className="sr-only">No collection target</span>
            </>}
          </dd>
        </div>
        </>}
      </dl>
      <div>
        {target != null && target > 0 && <progress className="collection-progress h-2 w-full" aria-label="Posts saved toward collection goal" max={target} value={Math.min(dataset.record_count, target)} />}
        <p className="mt-1 text-sm text-muted">
          {target != null && <>Goal: {target.toLocaleString()} posts</>}
          {firstBatch != null && <>{target != null && " · "}First request: {firstBatch.toLocaleString()} saved</>}
          {dataset.source_type !== "x" && (dataset.source_type === "csv" ? "Imported from CSV" : "Sample dataset")}
        </p>
      </div>
      <p role="status" className={busy ? "text-sm text-muted" : "sr-only"}>
        {busy ? "Request in progress. Showing the last saved total." : message || `${dataset.record_count.toLocaleString()} posts saved${remaining == null ? "." : `; ${remaining.toLocaleString()} still to collect.`}`}
      </p>
      {!busy && dataset.truncated_reason && <p className="rounded-md bg-warn-bg px-3 py-2 text-sm text-warn-ink">Collection stopped: {dataset.truncated_reason}.</p>}
      {children}
      <details className="border-t border-rule pt-4 text-sm">
        <summary className="cursor-pointer text-muted">Search &amp; cost details</summary>
        <div className="mt-3 flex flex-col gap-3 text-muted">
          {dataset.query && <p className="break-words"><strong className="text-ink">Topic:</strong> {dataset.query}</p>}
          {policy ? <>
            <p>{policy.start_date} to {policy.end_date} · {policy.timezone} · Both dates included</p>
            <p>Days are searched from start to end; X returns newest posts first within each day. More posts are appended using saved progress.</p>
            <p>The goal is split across days. A day with fewer matches can leave the collection short.</p>
          </> : dataset.window_start && <p>{new Date(dataset.window_start).toLocaleString()} to {dataset.window_end ? new Date(dataset.window_end).toLocaleString() : "now"} (end excluded)</p>}
          {dataset.source_type === "x" && <>
            <p>{dataset.billed_reads == null ? "Provider reads not recorded" : `${dataset.billed_reads.toLocaleString()} provider reads`}{dataset.committed_cost_usd != null && ` · $${dataset.committed_cost_usd.toFixed(2)} estimated total cost`}</p>
            <p>Counts above are saved posts. Repeated IDs and unusable results can make billed reads higher. Page minimums can exceed the collection goal.</p>
            {firstBatch == null && <p>The first request count was not recorded for this older collection.</p>}
          </>}
          {!!policy && !!dataset.consumer_counts?.days.length && <dl className="grid grid-cols-2 gap-x-4 gap-y-1">
            {dataset.consumer_counts.days.map((day) => <div key={day.day} className="contents"><dt>{day.day}</dt><dd className="tnum text-right">{day.candidates} saved</dd></div>)}
          </dl>}
        </div>
      </details>
    </section>
  );
}
