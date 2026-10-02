import { useId, useState } from "react";

import type { ConsumerCounts, DatasetSummary } from "../../api/types";
import { useRetryCountdown } from "../../hooks/useRetryCountdown";

interface Props {
  dataset: DatasetSummary;
  counts?: ConsumerCounts;
  stage?: "collect" | "review";
  goal?: "kept" | "labeled";
  busy: boolean;
  costPerRead?: number | null;
  onRequest: (candidateTarget: number) => void;
}

/** One explicit paid batch at a time; a shortfall never triggers collection itself. */
export function AdditionalCandidates({
  dataset,
  counts = dataset.consumer_counts ?? undefined,
  stage = "review",
  goal = "labeled",
  busy,
  costPerRead,
  onRequest,
}: Props) {
  const [choice, setChoice] = useState<{ basis: string; amount: number } | null>(null);
  const retrySeconds = useRetryCountdown(dataset.retry_at);
  const costId = useId();
  if (!counts || !dataset.consumer_policy) return null;

  const target = dataset.candidate_target ?? dataset.record_count;
  const base = Math.max(target, dataset.record_count);
  const capacity = Math.max(0, 5000 - base);
  const shortfall = goal === "kept"
    ? Math.max(0, counts.reviewed_target - counts.included)
    : counts.shortfall;
  const pending = counts.pending_eligibility + (goal === "labeled" ? counts.pending_sentiment : 0);
  // A completed batch or changed shortfall gets a fresh default, while edits survive rerenders.
  const basis = `${dataset.dataset_id}-${target}-${goal}-${shortfall}-${pending}`;
  const amount = choice?.basis === basis
    ? choice.amount
    : Math.min(Math.max(1, shortfall - pending), capacity);
  const resume = dataset.partial;
  const invalid = !resume && (!Number.isInteger(amount) || amount < 1 || amount > capacity);
  const estimateReads = resume ? Math.max(0, target - dataset.record_count) : amount;

  // Collect only finishes the authorized goal. Replacement batches belong to review.
  if (stage === "collect" && (!resume || shortfall === 0)) return null;
  let unavailable: string | null = null;
  if (shortfall === 0) unavailable = goal === "kept" ? "Kept-post target reached." : "Reviewed target reached.";
  else if (!resume && pending >= shortfall)
    unavailable = `${pending.toLocaleString()} saved ${pending === 1 ? "post still needs" : "posts still need"} review. Get more if exclusions leave you short.`;
  else if (!resume && !dataset.can_collect_more)
    unavailable = "No more posts are available for this saved search. You can continue with a partial dataset.";
  else if (!resume && capacity === 0)
    unavailable = "The 5,000-post collection limit has been reached.";

  return (
    <section
      className={stage === "collect" ? "flex flex-col gap-3" : "glass-panel flex flex-col gap-3 p-4"}
      aria-label={stage === "collect"
        ? "Get more posts"
        : goal === "kept" ? "Complete kept-post target" : "Complete reviewed target"}
    >
      {unavailable ? <p role="status" className="text-sm">{unavailable}</p> : <>
        {stage === "review" && <>
          <h3 className="font-semibold">
            {shortfall.toLocaleString()} more {goal === "kept" ? "kept" : "reviewed"} posts needed
          </h3>
          {!!pending && (
            <p className="text-sm text-muted">
              {pending.toLocaleString()} saved {pending === 1 ? "post still needs" : "posts still need"} review.
            </p>
          )}
        </>}
        {!resume && (
          <label className="flex flex-col gap-1 text-sm">
            Posts to request
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
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            className="btn"
            aria-describedby={costId}
            disabled={busy || invalid || retrySeconds > 0}
            onClick={() => onRequest(resume ? target : base + amount)}
          >
            Get more posts
          </button>
          <p id={costId} className="text-xs text-muted">
            Paid X request · Estimate: {costPerRead == null || invalid || estimateReads <= 0
              ? "unavailable"
              : `$${(estimateReads * costPerRead).toFixed(2)}`}
          </p>
        </div>
        {retrySeconds > 0 && (
          <p className="text-sm text-warn-ink">
            X rate limit · Try again in <span className="tnum">
              {Math.floor(retrySeconds / 60)}:{String(retrySeconds % 60).padStart(2, "0")}
            </span>
          </p>
        )}
        <p className="text-xs text-muted">
          Adds to this dataset using the same search and spend caps. Actual cost may vary.
        </p>
      </>}
    </section>
  );
}
