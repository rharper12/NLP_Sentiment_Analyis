import { useCallback, useEffect, useRef, useState } from "react";

import { api, isAbort } from "../../api/client";
import type { LabelEstimate, LabelSummary } from "../../api/types";
import { toError, useAsync } from "../../hooks/useAsync";
import { ConfirmDialog } from "../label/ConfirmDialog";
import { CostChip } from "../label/CostChip";
import { ReviewChoice } from "../label/ReviewChoice";
import { Reviewer } from "../label/Reviewer";
import { SLICE, usd } from "../label/shared";
import { SummaryBlock } from "../label/SummaryBlock";
import { Notice } from "../ui/Notice";
import { Skeleton, SkeletonLines } from "../ui/Skeleton";

interface Props {
  datasetId: string;
  /** Operator diagnostics (only true for local runs); gates every operator-only message. */
  diagnostics: boolean;
  comprehendEnabled: boolean | null;
  checkpointLocation: "local" | "s3" | null;
  onBack: () => void;
  onContinue: () => void;
}


type Phase = "method" | "labelling" | "review-choice" | "reviewing" | "summary";

/**
 * Stage 4. Comprehend labels everything cheaply; a person reviews some or all of it. Every paid
 * slice is saved server-side before the next starts, and reviewer decisions are flushed in
 * small batches, so a crash or a closed tab never loses more than a few seconds of work.
 */
export function LabelStep({ datasetId, diagnostics, comprehendEnabled, checkpointLocation, onBack, onContinue }: Props) {
  const summary = useAsync<LabelSummary>();
  const estimate = useAsync<LabelEstimate>();
  const { run: loadSummary } = summary;
  const { run: loadEstimate } = estimate;
  const [phase, setPhase] = useState<Phase>("method");
  const [error, setError] = useState<Error | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [progress, setProgress] = useState<{ done: number; total: number; spent: number | null } | null>(null);
  const labelling = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    await Promise.all([
      loadSummary((signal) => api.labelSummary(datasetId, signal)),
      loadEstimate((signal) => api.labelEstimate(datasetId, signal)),
    ]);
  }, [datasetId, loadSummary, loadEstimate]);

  useEffect(() => { void refresh(); }, [refresh]);

  const runComprehend = async () => {
    setConfirmOpen(false);
    setPhase("labelling");
    setError(null);
    const controller = new AbortController();
    labelling.current = controller;
    const total = estimate.data?.records_to_send ?? 0;
    let done = 0, spent: number | null = 0;
    setProgress({ done, total, spent });
    try {
      for (;;) {
        const p = await api.labelComprehend(datasetId, SLICE, controller.signal);
        done += p.labelled_in_call;
        spent = p.cost_usd == null || spent == null ? null : spent + p.cost_usd;
        setProgress({ done, total, spent });
        if (p.done || p.labelled_in_call === 0) break;
      }
    } catch (e) {
      if (!isAbort(e)) setError(toError(e));
    } finally {
      labelling.current = null;
      await refresh();
      setPhase("review-choice");
    }
  };

  const est = estimate.data, sum = summary.data;
  const allLabelled = sum ? sum.labelled === sum.total : false;

  return (
    <section className="mx-auto flex max-w-4xl flex-col gap-6">
      <div>
        <h2 className="text-2xl font-semibold tracking-tight">Label the posts</h2>
        <p className="mt-1 text-muted">Task 2 trains and scores a model against known labels. Comprehend gives every post a label in seconds; reviewing a sample by hand tells you how far to trust it.</p>
      </div>

      <Notice error={error ?? summary.error ?? estimate.error} />

      {sum && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="tnum rounded-full border border-rule px-3 py-1"><strong>{sum.labelled.toLocaleString()}</strong> of {sum.total.toLocaleString()} labelled</span>
          {Object.entries(sum.by_source).map(([k, v]) => <span key={k} className="tnum rounded-full bg-surface-2 px-3 py-1 text-muted">{v.toLocaleString()} {k}</span>)}
          {diagnostics && checkpointLocation && <span className="text-xs text-muted">Snapshots saved to {checkpointLocation === "s3" ? "S3" : "data/checkpoints"} after every paid step.</span>}
        </div>
      )}

      {phase === "method" && (
        <div className="grid gap-4 md:grid-cols-2">
          <div className="glass-panel flex flex-col gap-3 p-5">
            <h3 className="font-semibold">Label with Comprehend</h3>
            <p className="text-sm text-muted">Amazon Comprehend assigns positive, negative, neutral or mixed with a confidence score. Fast and cheap; your Task 2 model will be learning to imitate it, so review a sample afterwards.</p>
            {!est && <Skeleton className="h-8 w-56" />}
            {est && <CostChip est={est} />}
            <p className="text-xs text-muted">Billed per {est?.unit_chars ?? 100} characters with a {est?.min_units_per_document ?? 3}-unit minimum per post. Posts already sent to Comprehend are never re-billed.</p>
            {diagnostics && comprehendEnabled === false && <p className="text-xs text-warn-ink">Comprehend is disabled on the server (COMPREHEND_ENABLED=false).</p>}
            <button type="button" className="btn-primary mt-auto" disabled={comprehendEnabled === false || !est || est.records_to_send === 0} onClick={() => setConfirmOpen(true)}>
              {est && est.records_to_send === 0 ? "All posts already labelled" : "Label with Comprehend…"}
            </button>
          </div>
          <div className="glass-panel flex flex-col gap-3 p-5">
            <h3 className="font-semibold">Label manually</h3>
            <p className="text-sm text-muted">Read each post and pick the label yourself. Free, slower, and the most trustworthy ground truth. You can label all posts or a sample.</p>
            <button type="button" className="btn mt-auto" onClick={() => setPhase("review-choice")}>Choose what to label by hand</button>
          </div>
          {allLabelled && (
            <div className="md:col-span-2 flex flex-wrap items-center justify-between gap-3 border-t border-rule pt-4">
              <p className="text-sm text-muted">Every post already has a label{sum?.by_source.source ? " from the source dataset" : ""}. You can still review a sample, or skip ahead.</p>
              <button type="button" className="btn-primary" onClick={onContinue}>Skip to Export →</button>
            </div>
          )}
        </div>
      )}

      {phase === "labelling" && progress && (
        <div className="glass-panel flex flex-col gap-3 p-5" aria-live="polite">
          <div className="flex items-center justify-between"><h3 className="font-semibold">Labelling with Comprehend…</h3><button type="button" className="btn" onClick={() => labelling.current?.abort()}>Cancel</button></div>
          <div className="h-2 w-full overflow-hidden rounded bg-surface-2"><div className="h-full bg-accent transition-[width] duration-300" style={{ width: `${progress.total ? Math.min(100, (100 * progress.done) / progress.total) : 100}%` }} /></div>
          <p className="tnum text-sm text-muted">{progress.done.toLocaleString()} of {progress.total.toLocaleString()} posts{progress.spent != null && ` · ${usd(progress.spent)} so far`}. Each batch is saved before the next starts; cancelling keeps what is done.</p>
        </div>
      )}

      {phase === "review-choice" && <ReviewChoice total={sum?.total ?? 0} onChoose={async (mode, size, unit) => {
        setError(null);
        try {
          const s = await api.chooseReview(datasetId, mode, size, unit);
          summary.run(() => Promise.resolve(s));
          setPhase(mode === "none" ? "summary" : "reviewing");
        } catch (e) { setError(toError(e)); }
      }} onBack={() => setPhase("method")} />}

      {phase === "reviewing" && <Reviewer datasetId={datasetId} onError={setError} onDone={async () => { await refresh(); setPhase("summary"); }} />}

      {phase === "summary" && (
        <div className="glass-panel flex flex-col gap-4 p-5">
          <h3 className="font-semibold">Labels ready</h3>
          {sum ? <SummaryBlock s={sum} /> : <SkeletonLines lines={4} />}
          <div className="flex flex-wrap justify-between gap-3 border-t border-rule pt-4">
            <div className="flex gap-2"><button type="button" className="btn" onClick={() => setPhase("method")}>Label more</button><button type="button" className="btn" onClick={() => setPhase("review-choice")}>Review more</button></div>
            <button type="button" className="btn-primary" onClick={onContinue}>Continue to Export →</button>
          </div>
        </div>
      )}

      {phase === "method" && <button type="button" className="btn-link self-start" onClick={onBack}>← Back to Analyze</button>}

      {confirmOpen && est && (
        <ConfirmDialog est={est} onCancel={() => setConfirmOpen(false)} onConfirm={() => void runComprehend()} />
      )}
    </section>
  );
}
