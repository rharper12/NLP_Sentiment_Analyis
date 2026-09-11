import { useEffect, useRef } from "react";

import { api } from "../api/client";
import type { HistoryRun } from "../api/types";
import { useAsync } from "../hooks/useAsync";
import { Notice } from "./ui/Notice";
import { SkeletonLines } from "./ui/Skeleton";

const when = (iso: string) => new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

/** Past runs from the database. Metadata only. */
export default function HistoryDialog({ ephemeral, onClose }: { ephemeral: boolean; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const runs = useAsync<HistoryRun[]>();
  useEffect(() => { ref.current?.showModal(); void runs.run(api.history); }, [runs.run]);

  return (
    <dialog ref={ref} onClose={onClose} aria-labelledby="history-heading" className="glass-panel m-auto w-[min(900px,94vw)] p-5 text-ink shadow-2xl backdrop:bg-transparent sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 id="history-heading" className="font-semibold">Recent runs</h3>
        <button type="button" className="btn-link" onClick={onClose}>Close</button>
      </div>
      {ephemeral && <p className="mt-2 text-xs text-warn-ink">History resets on each cold start (SQLite on /tmp). Set DATABASE_URL to keep it.</p>}
      <Notice error={runs.error} className="mt-3" />
      <div className="mt-4">
        {runs.loading && !runs.data && <SkeletonLines lines={5} />}
        {runs.data?.length === 0 && <p className="text-sm text-muted">No runs yet. Each pipeline run is recorded here.</p>}
        {runs.data && runs.data.length > 0 && (
          <ul className="divide-y divide-rule text-sm">
            {runs.data.map((r) => (
              <li key={`${r.dataset_id}-${r.created_at}`} className="grid gap-1 py-3 sm:grid-cols-[110px_1fr_auto]">
                <span className="tnum text-muted">{when(r.created_at)}</span>
                <span className="min-w-0"><span className="font-medium">{r.source_type}</span>{r.query && <span className="text-muted"> · {r.query.length > 48 ? `${r.query.slice(0, 48)}…` : r.query}</span>}<span className="block text-xs text-muted">{r.applied_steps.map((s) => s.replace("_", " ")).join(" → ")}</span></span>
                <span className="tnum text-right text-xs text-muted">{r.vocab_before.toLocaleString()} → {r.vocab_after.toLocaleString()} vocab{r.sentiment_agreement != null && ` · ${(r.sentiment_agreement * 100).toFixed(0)}% kept`}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </dialog>
  );
}
