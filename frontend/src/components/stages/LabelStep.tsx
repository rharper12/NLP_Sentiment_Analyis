import { useCallback, useEffect, useRef, useState } from "react";

import { api, isAbort } from "../../api/client";
import type { DatasetSummary, LabelEstimate, LabelSummary } from "../../api/types";
import { ConsumerReviewer } from "../label/ConsumerReviewer";
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
  onReviewActiveChange?: (active: boolean) => void;
  onBack: () => void;
  onContinue: () => void;
  consumerDataset?: DatasetSummary;
  collectionLoading?: boolean;
  collectionError?: Error | null;
  collectionMessage?: string;
  costPerRead?: number | null;
  onAdditional?: (candidateTarget: number) => void;
  onChanged?: (dataset: DatasetSummary) => void;
}

type Phase = "method" | "labelling" | "review-choice" | "reviewing" | "summary";

/**
 * General labeling combines optional Comprehend labels with manual review. Paid batches are saved
 * server-side. Manual decisions are queued in Reviewer and must finish saving before exit.
 */
export function LabelStep(props: Props) {
  if (props.consumerDataset?.consumer_policy)
    return (
      <ConsumerReviewer
        key={props.datasetId}
        datasetId={props.datasetId}
        policy={props.consumerDataset.consumer_policy}
        collection={props.consumerDataset}
        collectionLoading={props.collectionLoading}
        collectionError={props.collectionError}
        collectionMessage={props.collectionMessage}
        costPerRead={props.costPerRead}
        onAdditional={props.onAdditional}
        onChanged={props.onChanged}
        onReviewActiveChange={props.onReviewActiveChange}
        onBack={props.onBack}
        onContinue={props.onContinue}
      />
    );
  // A different dataset owns a fresh review queue, progress state and request lifetime.
  return <LabelSession key={props.datasetId} {...props} />;
}

function LabelSession({
  datasetId,
  diagnostics,
  comprehendEnabled,
  checkpointLocation,
  onReviewActiveChange,
  onBack,
  onContinue,
}: Props) {
  const summary = useAsync<LabelSummary>();
  const estimate = useAsync<LabelEstimate>();
  const { run: loadSummary } = summary;
  const { run: loadEstimate } = estimate;
  const [phase, setPhase] = useState<Phase>("method");
  const [error, setError] = useState<Error | null>(null);
  const [persistenceWarnings, setPersistenceWarnings] = useState<string[]>([]);
  const [failed, setFailed] = useState(0);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [progress, setProgress] = useState<{
    done: number;
    total: number;
    spent: number | null;
  } | null>(null);
  const labelling = useRef<AbortController | null>(null);
  useEffect(
    () => () => {
      const previous = labelling.current;
      labelling.current = null;
      previous?.abort();
    },
    [],
  );

  const refresh = useCallback(async () => {
    return Promise.all([
      loadSummary((signal) => api.labelSummary(datasetId, signal)),
      loadEstimate((signal) => api.labelEstimate(datasetId, signal)),
    ]);
  }, [datasetId, loadSummary, loadEstimate]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const runComprehend = async () => {
    if (labelling.current) return;
    setConfirmOpen(false);
    setPhase("labelling");
    setError(null);
    const controller = new AbortController();
    labelling.current = controller;
    const owns = () => labelling.current === controller && !controller.signal.aborted;
    const total = estimate.data?.records_to_send ?? 0;
    let done = 0,
      spent: number | null = 0;
    setProgress({ done, total, spent });
    try {
      while (owns()) {
        const p = await api.labelComprehend(datasetId, SLICE, controller.signal);
        if (!owns()) return;
        done = Math.min(total, done + p.labelled_in_call);
        setFailed(p.failed_total ?? 0);
        setPersistenceWarnings(p.warnings ?? []);
        spent = p.cost_usd == null || spent == null ? null : spent + p.cost_usd;
        setProgress({ done, total, spent });
        if (p.done || p.labelled_in_call === 0) break;
      }
    } catch (e) {
      if (owns() && !isAbort(e)) setError(toError(e));
    } finally {
      if (owns()) {
        await refresh();
        if (owns()) {
          labelling.current = null;
          setPhase("review-choice");
        }
      }
    }
  };

  const cancelLabelling = () => {
    const previous = labelling.current;
    labelling.current = null;
    previous?.abort();
    setPhase("review-choice");
    void refresh();
  };

  const est = estimate.data,
    sum = summary.data;
  const allLabelled = sum ? sum.labelled === sum.total : false;

  return (
    <section className="mx-auto flex max-w-4xl flex-col gap-6">
      <div>
        <h2 className="text-2xl font-semibold tracking-tight">Label the posts</h2>
        <p className="mt-1 text-muted">
          Comprehend suggests labels for the overall sentiment of each post. Review uncertain
          predictions and save your own labels before using them in a model. These labels do not
          necessarily describe sentiment toward a particular product.
        </p>
        <p className="mt-2 text-sm text-muted">
          This dataset uses sentiment-only labeling. Include/Exclude eligibility review is available
          for datasets collected with the Consumer reactions option.
        </p>
      </div>

      <Notice
        error={error ?? summary.error ?? estimate.error}
        warnings={
          summary.error ? persistenceWarnings : (summary.data?.warnings ?? persistenceWarnings)
        }
      />
      {Math.max(failed, estimate.data?.failed_total ?? 0) > 0 && (
        <p role="status" className="text-sm text-warn-ink">
          {Math.max(failed, estimate.data?.failed_total ?? 0)} posts have no successful Comprehend
          result. Successful labels were saved. Permanently rejected posts are skipped; temporary
          failures can be retried up to three attempts. You can label these posts manually.
        </p>
      )}

      {(est?.prefix_labels ?? 0) > 0 && (
        <p role="status" className="text-sm text-warn-ink">
          {est?.prefix_labels} Comprehend labels describe only the first 5,000 UTF-8 bytes of
          oversized documents. Complete text is retained.
        </p>
      )}

      {sum && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="tnum rounded-full border border-rule px-3 py-1">
            <strong>{sum.labelled.toLocaleString()}</strong> of {sum.total.toLocaleString()}{" "}
            labelled
          </span>
          {Object.entries(sum.by_source).map(([k, v]) => (
            <span key={k} className="tnum rounded-full bg-surface-2 px-3 py-1 text-muted">
              {v.toLocaleString()} {k}
            </span>
          ))}
          {diagnostics && checkpointLocation && (
            <span className="text-xs text-muted">
              Snapshot destination: {checkpointLocation === "s3" ? "S3" : "data/checkpoints"}. Check
              Export for persistence status.
            </span>
          )}
        </div>
      )}

      {phase === "method" && (
        <div className="grid gap-4 md:grid-cols-2">
          <div className="glass-panel flex flex-col gap-3 p-5">
            <h3 className="font-semibold">Label with Comprehend</h3>
            <p className="text-sm text-muted">
              Amazon Comprehend assigns positive, negative, neutral or mixed with a confidence
              score. Fast and cheap; your Task 2 model will be learning to imitate it, so review a
              sample afterwards.
            </p>
            {!est && <Skeleton className="h-8 w-56" />}
            {est && <CostChip est={est} />}
            <p className="text-xs text-muted">
              Billed per {est?.unit_chars ?? 100} characters with a{" "}
              {est?.min_units_per_document ?? 3}-unit minimum per post. Successfully labelled posts
              are skipped. Retrying a temporary failure may incur another charge.
            </p>
            {diagnostics && comprehendEnabled === false && (
              <p className="text-xs text-warn-ink">
                Comprehend is disabled on the server (COMPREHEND_ENABLED=false).
              </p>
            )}
            <button
              type="button"
              className="btn-primary mt-auto"
              disabled={comprehendEnabled === false || !est || est.records_to_send === 0}
              onClick={() => setConfirmOpen(true)}
            >
              {est && est.records_to_send === 0
                ? "No eligible posts to send"
                : "Label with Comprehend…"}
            </button>
          </div>
          <div className="glass-panel flex flex-col gap-3 p-5">
            <h3 className="font-semibold">Label manually</h3>
            <p className="text-sm text-muted">
              Read each post and pick the label yourself. Use consistent criteria and check
              ambiguous posts; human labels can also be uncertain. You can label all posts or a
              sample.
            </p>
            <button
              type="button"
              className="btn mt-auto"
              disabled={!sum}
              onClick={() => setPhase("review-choice")}
            >
              Choose what to label by hand
            </button>
          </div>
          {allLabelled && (
            <div className="md:col-span-2 flex flex-wrap items-center justify-between gap-3 border-t border-rule pt-4">
              <p className="text-sm text-muted">
                Every post already has a label
                {sum?.by_source.source ? " from the source dataset" : ""}. You can still review a
                sample, or skip ahead.
              </p>
              <button type="button" className="btn-primary" onClick={onContinue}>
                Skip to Export →
              </button>
            </div>
          )}
        </div>
      )}

      {phase === "labelling" && progress && (
        <div className="glass-panel flex flex-col gap-3 p-5" aria-live="polite">
          <div className="flex items-center justify-between">
            <h3 className="font-semibold">Labelling with Comprehend…</h3>
            <button type="button" className="btn" onClick={cancelLabelling}>
              Cancel
            </button>
          </div>
          <div className="h-2 w-full overflow-hidden rounded bg-surface-2">
            <div
              className="h-full bg-accent transition-[width] duration-300"
              style={{
                width: `${progress.total ? Math.min(100, (100 * progress.done) / progress.total) : 100}%`,
              }}
            />
          </div>
          <p className="tnum text-sm text-muted">
            {progress.done.toLocaleString()} of {progress.total.toLocaleString()} posts
            {progress.spent != null && ` · ${usd(progress.spent)} so far`}. Each batch is saved
            before the next starts; cancelling keeps what is done.
          </p>
        </div>
      )}

      {phase === "review-choice" && (
        <ReviewChoice
          total={sum?.total ?? 0}
          unreviewed={(sum?.total ?? 0) - (sum?.manually_reviewed ?? 0)}
          machineScored={sum?.machine_scored ?? 0}
          onChoose={async (mode, size, unit) => {
            setError(null);
            const s = await summary.run((signal) =>
              api.chooseReview(datasetId, mode, size, unit, signal),
            );
            if (!s) return;
            onReviewActiveChange?.(mode !== "none");
            setPhase(mode === "none" ? "summary" : "reviewing");
          }}
          onBack={() => setPhase("method")}
        />
      )}

      {phase === "reviewing" && (
        <Reviewer
          datasetId={datasetId}
          onError={setError}
          onDone={async () => {
            const [s] = await refresh();
            if (!s) return;
            onReviewActiveChange?.(false);
            setPhase("summary");
          }}
        />
      )}

      {phase === "summary" && (
        <div className="glass-panel flex flex-col gap-4 p-5">
          <h3 className="font-semibold">
            {sum && sum.review_sample_size > 0
              ? sum.reviewed === sum.review_sample_size
                ? "Review complete — labels saved"
                : "Review paused — labels saved"
              : "Label summary"}
          </h3>
          {sum && sum.review_sample_size > 0 && (
            <p role="status" className="text-sm">
              {sum.reviewed} of {sum.review_sample_size} selected posts have saved manual labels.{" "}
              {sum.reviewed < sum.review_sample_size
                ? "You can return to this review or export the labels saved so far."
                : "You can export now or choose more posts to review."}
            </p>
          )}
          {sum && sum.review_sample_size > 0 && (
            <button
              type="button"
              className="btn self-start"
              onClick={() => {
                onReviewActiveChange?.(true);
                setPhase("reviewing");
              }}
            >
              Return to selected review
            </button>
          )}
          {sum ? <SummaryBlock s={sum} /> : <SkeletonLines lines={4} />}
          <div className="flex flex-wrap justify-between gap-3 border-t border-rule pt-4">
            <div className="flex gap-2">
              <button type="button" className="btn" onClick={() => setPhase("method")}>
                Label more
              </button>
              <button type="button" className="btn" onClick={() => setPhase("review-choice")}>
                Review more
              </button>
            </div>
            <button type="button" className="btn-primary" onClick={onContinue}>
              Continue to Export →
            </button>
          </div>
        </div>
      )}

      {phase === "method" && (
        <button type="button" className="btn-link self-start" onClick={onBack}>
          ← Back to Analyze
        </button>
      )}

      {confirmOpen && est && (
        <ConfirmDialog
          est={est}
          onCancel={() => setConfirmOpen(false)}
          onConfirm={() => void runComprehend()}
        />
      )}
    </section>
  );
}
