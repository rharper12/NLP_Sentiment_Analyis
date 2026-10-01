import { useState } from "react";

import type { ConsumerCounts, DatasetSummary } from "../../api/types";

interface Props {
  dataset: DatasetSummary;
  counts?: ConsumerCounts;
  busy: boolean;
  costPerRead?: number | null;
  onRequest: (candidateTarget: number) => void;
}

/** One explicit paid batch at a time; a review shortfall never triggers collection itself. */
export function AdditionalCandidates({
  dataset,
  counts = dataset.consumer_counts ?? undefined,
  busy,
  costPerRead,
  onRequest,
}: Props) {
  const [choice, setChoice] = useState<{ basis: string; amount: number } | null>(null);
  if (!counts || !dataset.consumer_policy) return null;

  const target = dataset.candidate_target ?? dataset.record_count;
  const base = Math.max(target, dataset.record_count);
  const capacity = Math.max(0, 5000 - base);
  // A completed batch or changed shortfall gets a fresh default, while edits survive rerenders.
  const basis = `${dataset.dataset_id}-${target}-${counts.shortfall}`;
  const amount = choice?.basis === basis ? choice.amount : Math.min(counts.shortfall, capacity);
  const resume = dataset.partial;
  const unavailable = !resume && !dataset.can_collect_more;
  const capped = !resume && capacity === 0;
  const invalid = !resume && (!Number.isInteger(amount) || amount < 1 || amount > capacity);
  const estimateReads = resume ? Math.max(0, target - dataset.record_count) : amount;

  return (
    <section className="glass-panel flex flex-col gap-3 p-4" aria-label="Complete reviewed target">
      {counts.shortfall === 0 ? (
        <p role="status" className="font-medium">
          Reviewed target reached. Additional collection is no longer needed.
        </p>
      ) : (
        <>
          <h3 className="font-semibold">
            Still need {counts.shortfall.toLocaleString()} reviewed records
          </h3>
          <p className="text-sm text-muted">
            {counts.reviewed_final.toLocaleString()} of {counts.reviewed_target.toLocaleString()}{" "}
            are fully reviewed. Request another candidate batch, review it, and repeat until the
            target is reached. New candidates still need eligibility and sentiment review.
          </p>
          {!!(counts.pending_eligibility + counts.pending_sentiment) && (
            <p className="text-sm text-muted">
              Existing candidates have {counts.pending_eligibility} eligibility reviews and{" "}
              {counts.pending_sentiment} sentiment reviews pending. Reviewing these may reduce the
              shortfall without another request.
            </p>
          )}
          {dataset.truncated_reason && (
            <p className="text-sm text-muted">Collection stopped: {dataset.truncated_reason}.</p>
          )}
          {unavailable ? (
            <p role="status">
              Additional collection is unavailable for this saved search. Review remaining
              candidates or export the partial dataset; the dates will not be expanded
              automatically.
            </p>
          ) : capped ? (
            <p role="status">
              The 5,000-candidate target limit has been reached. Review remaining candidates or
              export the partial dataset.
            </p>
          ) : (
            <>
              {resume ? (
                <p className="text-sm">
                  The last authorized batch is unfinished. Resume its saved target of{" "}
                  {target.toLocaleString()} candidates before increasing the quota.
                </p>
              ) : (
                <label className="flex flex-col gap-1 text-sm">
                  Additional candidate quota
                  <input
                    className="field w-36"
                    type="number"
                    min={1}
                    max={capacity}
                    step={1}
                    disabled={busy}
                    value={amount}
                    onChange={(event) => setChoice({ basis, amount: Number(event.target.value) })}
                  />
                </label>
              )}
              {dataset.retry_at != null && (
                <p className="text-sm">
                  Provider retry time: {new Date(dataset.retry_at * 1000).toLocaleString()}. Earlier
                  requests will keep the saved pause.
                </p>
              )}
              <p className="text-xs text-muted">
                This click authorizes a paid request using the saved dates, query and cumulative
                spend caps. Estimate for the {resume ? "remaining" : "additional"} quota:{" "}
                {costPerRead == null || invalid || estimateReads <= 0
                  ? "unavailable"
                  : `$${(estimateReads * costPerRead).toFixed(2)}`}
                . Filtering, daily quotas and page minimums can change the number returned and
                increase reads.
              </p>
              {capacity < counts.shortfall && !resume && (
                <p className="text-xs text-muted">
                  This batch is limited to {capacity} more candidate slots by the collection limit.
                </p>
              )}
              <button
                type="button"
                className="btn self-start"
                disabled={busy || invalid}
                onClick={() => onRequest(resume ? target : base + amount)}
              >
                {resume ? "Resume candidate request" : "Collect additional candidates"}
              </button>
            </>
          )}
        </>
      )}
    </section>
  );
}
