import { useEffect, useId, useRef, useState } from "react";

import { api } from "../../api/client";
import {
  SENTIMENT_LABELS,
  type ConsumerPolicy,
  type DatasetSummary,
  type EligibilityItem,
  type EligibilityPage,
  type PostRecord,
} from "../../api/types";
import { toError, useAsync } from "../../hooks/useAsync";
import { AdditionalCandidates } from "../collect/AdditionalCandidates";
import { SkeletonLines } from "../ui/Skeleton";
import { Notice } from "../ui/Notice";

interface Props {
  datasetId: string;
  policy: ConsumerPolicy;
  onChanged?: (dataset: DatasetSummary) => void;
  onReviewActiveChange?: (active: boolean) => void;
  onBack: () => void;
  onContinue: () => void;
  collection?: DatasetSummary;
  collectionLoading?: boolean;
  collectionError?: Error | null;
  collectionMessage?: string;
  costPerRead?: number | null;
  onAdditional?: (candidateTarget: number) => void;
}

type Mode = "eligibility" | "sentiment";
type Phase = "choice" | "labels" | "cards";

/** Explicit actions save one original post; stable offsets make every decision revisitable. */
export function ConsumerReviewer({
  datasetId,
  policy,
  onChanged,
  onReviewActiveChange,
  onBack,
  onContinue,
  collection,
  collectionLoading = false,
  collectionError,
  collectionMessage,
  costPerRead,
  onAdditional,
}: Props) {
  const page = useAsync<EligibilityPage>();
  const { run: loadPage, cancel, reset } = page;
  const [phase, setPhase] = useState<Phase>("choice");
  const [mode, setMode] = useState<Mode>("sentiment");
  const [offset, setOffset] = useState<number | null>(null);
  const [revision, setRevision] = useState(0);
  const [saving, setSaving] = useState(false);
  const [failedDecision, setFailedDecision] = useState<EligibilityItem | null>(
    null,
  );
  const [saveStatus, setSaveStatus] = useState("");
  const [error, setError] = useState<Error | null>(null);
  const [loadedCount, setLoadedCount] = useState<number | undefined>();
  const inFlight = useRef(false);
  const mounted = useRef(true);
  const heading = useRef<HTMLHeadingElement>(null);
  const previousPhase = useRef(phase);
  const loadedOnce = useRef(false);
  const sentimentId = useId();
  const exclusionHintId = useId();
  const count = collection?.record_count;
  const [previousCount, setPreviousCount] = useState(count);
  if (previousCount !== count) {
    setPreviousCount(count);
    setOffset(null);
  }

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    if (phase !== "cards" || collectionLoading) return cancel;
    const startAt =
      offset === null
        ? mode === "sentiment"
          ? "first_unlabeled"
          : "first_unreviewed"
        : undefined;
    void loadPage((signal) =>
      api.eligibilityPage(datasetId, offset ?? 0, "all", signal, startAt),
    ).then((loaded) => {
      if (loaded && mounted.current) setLoadedCount(count);
    });
    return cancel;
  }, [
    datasetId,
    phase,
    mode,
    offset,
    count,
    revision,
    collectionLoading,
    loadPage,
    cancel,
  ]);

  useEffect(() => {
    if (phase !== previousPhase.current) {
      previousPhase.current = phase;
      heading.current?.focus();
    } else if (!page.loading && page.data) {
      if (loadedOnce.current) heading.current?.focus();
      loadedOnce.current = true;
    }
  }, [phase, page.loading, page.data]);

  const unsettled = saving || failedDecision !== null;
  useEffect(() => {
    onReviewActiveChange?.(unsettled);
    return () => onReviewActiveChange?.(false);
  }, [unsettled, onReviewActiveChange]);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (unsettled || inFlight.current) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [unsettled]);

  const refreshing =
    phase === "cards" &&
    !page.error &&
    (page.loading ||
      collectionLoading ||
      loadedCount !== count ||
      !page.data ||
      (offset !== null && page.data.offset !== offset));
  const busy = saving || refreshing || collectionLoading;
  const locked = busy || failedDecision !== null;
  const current = page.data?.offset ?? 0;
  const item = page.error || refreshing ? undefined : page.data?.items[0];
  const counts = page.data?.counts;
  const remaining = counts
    ? counts.pending_eligibility +
      (mode === "sentiment" ? counts.pending_sentiment : 0)
    : 0;

  const start = (nextMode: Mode) => {
    reset();
    setMode(nextMode);
    setOffset(null);
    setSaveStatus("");
    setError(null);
    setPhase("cards");
  };
  const move = (nextOffset: number | null) => {
    if (locked) return;
    setOffset(nextOffset);
    setRevision((value) => value + 1);
    setSaveStatus("");
    setError(null);
  };
  const save = async (decision: EligibilityItem) => {
    if (inFlight.current || busy || !item) return;
    inFlight.current = true;
    setSaving(true);
    setFailedDecision(null);
    setError(null);
    setSaveStatus("Saving…");
    try {
      const updated = await api.reviewEligibility(datasetId, [decision]);
      if (!mounted.current) return;
      setSaveStatus(
        decision.decision === "exclude"
          ? "Excluded. Saved."
          : decision.label
            ? `Kept as ${decision.label}. Saved.`
            : "Kept. Saved.",
      );
      onChanged?.(updated);
      setOffset(current + 1);
      setRevision((value) => value + 1);
    } catch (cause) {
      if (!mounted.current) return;
      setFailedDecision(decision);
      setError(toError(cause));
      setSaveStatus(
        "Not saved. Retry or discard this choice before moving on.",
      );
    } finally {
      inFlight.current = false;
      if (mounted.current) setSaving(false);
    }
  };

  return (
    <section className="mx-auto flex max-w-3xl flex-col gap-5">
      <div>
        <h2 className="text-3xl font-semibold tracking-tight">
          Review &amp; label
        </h2>
        {collection?.query && (
          <p className="mt-2 break-words text-sm text-muted">
            {collection.query}
          </p>
        )}
      </div>
      <Notice
        error={error ?? page.error ?? collectionError}
        warnings={collection?.warnings}
      />
      {phase !== "cards" ? (
        <div className="glass-panel flex flex-col gap-4 p-5 sm:p-7">
          <h3 ref={heading} tabIndex={-1} className="text-xl font-semibold">
            {phase === "choice"
              ? "Would you like to review each post?"
              : "Also label sentiment as you review?"}
          </h3>
          <p className="text-sm text-muted">
            {phase === "choice"
              ? "Keep the posts that fit your topic and exclude the rest."
              : "Label the posts you keep, or focus only on whether they belong."}
          </p>
          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              className="btn-primary min-h-11"
              disabled={collectionLoading}
              onClick={() =>
                phase === "choice" ? setPhase("labels") : start("sentiment")
              }
            >
              {phase === "choice" ? "Yes, review posts" : "Yes, review & label"}
            </button>
            <button
              type="button"
              className="btn min-h-11"
              disabled={collectionLoading}
              onClick={() =>
                phase === "choice" ? onContinue() : start("eligibility")
              }
            >
              {phase === "choice" ? "Skip for now →" : "No, review only"}
            </button>
          </div>
          <p className="text-xs text-muted">
            {phase === "choice"
              ? "Skipping leaves posts unreviewed and starts no automated labeling."
              : "Existing labels stay saved. You can add or change sentiment later."}
          </p>
          {phase === "labels" && (
            <button
              type="button"
              className="btn self-start"
              onClick={() => setPhase("choice")}
            >
              ← Back
            </button>
          )}
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
            {!refreshing && !page.error && counts ? (
              <p className="tnum text-muted">
                <strong className="text-ink">
                  {counts.human_inclusions + counts.human_exclusions}
                </strong>{" "}
                of {page.data?.total} reviewed
                {" · "}
                {counts.included} kept · {counts.human_exclusions}{" "}
                excluded
                {counts.author_cap_held > 0 && ` · ${counts.author_cap_held} over author limit`}
              </p>
            ) : (
              <p className="text-muted">
                {page.error
                  ? "Review progress unavailable"
                  : "Loading review progress…"}
              </p>
            )}
            <button
              type="button"
              className="btn"
              disabled={locked}
              onClick={() => setPhase("labels")}
            >
              Review settings
            </button>
          </div>
          <p role="status" className="sr-only">
            {collectionLoading
              ? "Collecting more posts…"
              : saveStatus || collectionMessage}
          </p>
          {page.error && (
            <button
              type="button"
              className="btn self-start"
              onClick={() => setRevision((value) => value + 1)}
            >
              Retry loading review
            </button>
          )}
          <div className="glass-panel overflow-hidden" aria-busy={busy}>
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-rule px-5 py-4 sm:px-7">
              <h3 ref={heading} tabIndex={-1} className="font-semibold">
                {item
                  ? "Review this post"
                  : refreshing
                    ? "Loading post…"
                    : "Review progress"}
              </h3>
              {!refreshing && !page.error && page.data && (
                <span className="tnum text-sm text-muted">
                  {item
                    ? `Post ${current + 1} of ${page.data.total}`
                    : `${page.data.total} posts`}
                </span>
              )}
            </div>
            <div className="flex flex-col gap-5 p-5 sm:p-7">
              {item ? (
                <>
                  <blockquote className="whitespace-pre-wrap break-words border-l-4 border-accent pl-5 text-lg leading-relaxed">
                    {item.text}
                  </blockquote>
                  <PostDetails item={item} timezone={policy.timezone} />
                  {item.eligibility_reviewed && (
                    <p className="text-sm text-muted">
                      Saved:{" "}
                      {item.eligibility === "exclude"
                        ? "excluded"
                        : `kept${item.sentiment_reviewed ? ` · ${item.label}` : ""}`}
                      .
                      {item.eligibility === "include" &&
                        !page.data?.included_ids.includes(item.id) &&
                        " Held by author limit."}
                    </p>
                  )}
                  {mode === "sentiment" && (
                    <div role="group" aria-labelledby={sentimentId}>
                      <p id={sentimentId} className="mb-2 text-sm font-medium">
                        Keep this post and label its sentiment
                      </p>
                      <p className="mb-3 text-xs text-muted">
                        Choosing a sentiment saves and opens the next post.
                      </p>
                      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                        {SENTIMENT_LABELS.map((label) => (
                          <button
                            type="button"
                            key={label}
                          className="selectable min-h-12 px-3 py-3 font-medium capitalize disabled:pointer-events-none disabled:opacity-45"
                            aria-pressed={
                              (failedDecision?.label ??
                                (item.sentiment_reviewed
                                  ? item.label
                                  : null)) === label
                            }
                            disabled={busy}
                            onClick={() =>
                              void save({
                                id: item.id,
                                decision: "include",
                                label,
                              })
                            }
                          >
                            {label}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                  <div className="flex flex-wrap gap-3">
                    <button
                      type="button"
                      className={
                        mode === "eligibility"
                          ? "btn-primary min-h-11"
                          : "btn min-h-11"
                      }
                      disabled={busy}
                      onClick={() =>
                        void save({ id: item.id, decision: "include" })
                      }
                    >
                      {mode === "sentiment" && !item.sentiment_reviewed
                        ? "Keep without a label"
                        : "Keep post"}
                    </button>
                    <button
                      type="button"
                      className="btn min-h-11"
                      aria-describedby={exclusionHintId}
                      disabled={busy}
                      onClick={() =>
                        void save({ id: item.id, decision: "exclude" })
                      }
                    >
                      Exclude post
                    </button>
                  </div>
                  <p id={exclusionHintId} className="text-xs text-muted">
                    No reason required. {mode === "eligibility" ? "Keep or Exclude" : "Exclude"}{" "}
                    saves and opens the next post.
                  </p>
                  {failedDecision && (
                    <div className="flex flex-wrap gap-3">
                      <button
                        type="button"
                        className="btn-primary"
                        disabled={busy}
                        onClick={() => void save(failedDecision)}
                      >
                        Retry save
                      </button>
                      <button
                        type="button"
                        className="btn"
                        disabled={busy}
                        onClick={() => {
                          setFailedDecision(null);
                          setError(null);
                          setSaveStatus("Unsaved choice discarded.");
                        }}
                      >
                        Discard unsaved choice
                      </button>
                    </div>
                  )}
                </>
              ) : refreshing ? (
                <>
                  <p role="status" className="text-sm text-muted">
                    Loading your review queue…
                  </p>
                  <SkeletonLines lines={4} />
                </>
              ) : page.error ? (
                <p>The post could not be loaded. Retry to continue.</p>
              ) : (
                <div className="py-4">
                  <p className="text-xl font-semibold">
                    {remaining ? "End of posts" : "Review complete"}
                  </p>
                  <p className="mt-2 text-sm text-muted">
                    Your saved decisions stay in place. You can go back to make
                    changes or continue to Clean.
                  </p>
                  {remaining > 0 && (
                    <button
                      type="button"
                      className="btn mt-4"
                      onClick={() => move(null)}
                    >
                      {mode === "sentiment"
                        ? "Finish remaining reviews"
                        : "Review remaining posts"}
                    </button>
                  )}
                </div>
              )}
              <div className="flex justify-between gap-3 border-t border-rule pt-4">
                <button
                  type="button"
                  className="btn"
                  disabled={locked || !!page.error || current === 0}
                  onClick={() => move(Math.max(0, current - 1))}
                >
                  ← Previous
                </button>
                <button
                  type="button"
                  className="btn"
                  disabled={
                    locked || !!page.error || current >= (page.data?.total ?? 0)
                  }
                  onClick={() => move(current + 1)}
                >
                  Next →
                </button>
              </div>
            </div>
          </div>
          {collection &&
            counts &&
            !refreshing &&
            !page.error &&
            onAdditional &&
            (!item || collection.partial) && (
              <AdditionalCandidates
                dataset={collection}
                counts={counts}
                goal={mode === "eligibility" ? "kept" : "labeled"}
                busy={locked}
                costPerRead={costPerRead}
                onRequest={onAdditional}
              />
            )}
          <details className="text-sm text-muted">
            <summary className="cursor-pointer">
              Labels &amp; export eligibility
            </summary>
            <p className="mt-2">
              Manual labels take priority over later Comprehend predictions.
              Reviewing does not start Comprehend. The fully reviewed export
              requires both a confirmed Keep decision and a manual sentiment
              label, within the author limit.
            </p>
          </details>
        </>
      )}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <button
          type="button"
          className="btn"
          disabled={locked}
          onClick={onBack}
        >
          ← Back to Collect
        </button>
        {phase === "cards" && (
          <button
            type="button"
            className="btn-primary"
            disabled={locked || !!page.error || !page.data}
            onClick={onContinue}
          >
            Continue to Clean →
          </button>
        )}
      </div>
    </section>
  );
}

function PostDetails({
  item,
  timezone,
}: {
  item: PostRecord;
  timezone: string;
}) {
  return (
    <details className="text-sm text-muted">
      <summary className="cursor-pointer">Post details</summary>
      <div className="mt-3 flex flex-col gap-2 break-words">
        <p>
          Record {item.id} · Author {item.author_id ?? "unavailable"}
        </p>
        <p>
          {item.created_at
            ? new Date(item.created_at).toLocaleString(undefined, {
                timeZone: timezone,
              })
            : "Timestamp unavailable"}{" "}
          ({timezone})
        </p>
        {item.screening && (
          <p>
            Screening suggestion: {item.screening.decision}. Your decision takes
            priority.
          </p>
        )}
        {!!item.screening?.evidence?.length && (
          <ul className="list-disc pl-5">
            {item.screening.evidence.map((evidence) => (
              <li key={evidence}>{evidence}</li>
            ))}
          </ul>
        )}
        {item.screening?.duplicate_of && (
          <p>Possible duplicate of {item.screening.duplicate_of}.</p>
        )}
        {!!item.eligibility_history?.length && (
          <details>
            <summary className="cursor-pointer">Previous decisions</summary>
            <ul className="mt-2">
              {item.eligibility_history.map((review, index) => (
                <li key={index}>
                  {review.reviewed_at}: {review.decision}
                  {review.reason && ` · ${review.reason}`}
                  {review.note && ` · ${review.note}`}
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
    </details>
  );
}
