import { useEffect, useId, useRef, useState } from "react";

import { api } from "../../api/client";
import {
  SENTIMENT_LABELS,
  type ConsumerPolicy,
  type DatasetSummary,
  type EligibilityItem,
  type EligibilityPage,
  type EligibilityReason,
  type PostRecord,
  type SentimentLabel,
} from "../../api/types";
import { toError, useAsync } from "../../hooks/useAsync";
import { AdditionalCandidates } from "../collect/AdditionalCandidates";
import { CollectionStatus } from "../collect/CollectionStatus";
import { Skeleton, SkeletonLines } from "../ui/Skeleton";
import { Notice } from "../ui/Notice";
import { ConsumerSummary } from "./ConsumerSummary";

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

const REASONS: [EligibilityReason, string][] = [
  ["news_or_article", "News or article distribution"],
  ["giveaway_or_promotion", "Giveaway or promotion"],
  ["technical_developer_content", "Technical implementation content"],
  ["duplicate_or_repeated_template", "Duplicate or repeated template"],
  ["off_topic", "Off topic"],
  ["insufficient_context", "Insufficient context"],
  ["not_english", "Not English"],
];

/** One original post, one save: eligibility and human sentiment travel together. */
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
  const queueLabel = useId();
  const { run: loadPage, cancel } = page;
  const [status, setStatus] = useState("needs_review");
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const inFlight = useRef(false);
  const mounted = useRef(true);
  const [saveStatus, setSaveStatus] = useState("");
  const [error, setError] = useState<Error | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const loadedOnce = useRef(false);
  const count = collection?.record_count;
  const [previousCount, setPreviousCount] = useState(count);
  // Reset the view before rendering a newly collected batch, while keeping saved decisions.
  if (previousCount !== count) {
    setPreviousCount(count);
    setStatus("needs_review");
    setOffset(0);
  }

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    if (collectionLoading) return cancel;
    void loadPage((signal) => api.eligibilityPage(datasetId, offset, status, signal)).then(
      (loaded) => {
        if (loaded && offset > 0 && offset >= loaded.total)
          setOffset(Math.max(0, loaded.total - 1));
      },
    );
    return cancel;
  }, [datasetId, offset, status, count, revision, collectionLoading, loadPage, cancel]);
  useEffect(() => {
    if (!page.loading && page.data) {
      if (loadedOnce.current) heading.current?.focus();
      loadedOnce.current = true;
    }
  }, [page.loading, page.data]);
  useEffect(() => {
    onReviewActiveChange?.(dirty || saving);
    return () => onReviewActiveChange?.(false);
  }, [dirty, saving, onReviewActiveChange]);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (dirty || inFlight.current) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const busy = saving || page.loading || collectionLoading;
  const locked = busy || dirty;
  const item = page.error ? undefined : page.data?.items[0];
  const save = async (decision: EligibilityItem) => {
    if (inFlight.current || busy) return;
    inFlight.current = true;
    setSaving(true);
    setError(null);
    setSaveStatus("Saving your review…");
    try {
      const updated = await api.reviewEligibility(datasetId, [decision]);
      if (!mounted.current) return;
      setDirty(false);
      setSaveStatus(
        decision.decision === "include"
          ? "Kept and labeled. Review saved."
          : "Excluded. Review saved; you can restore it from Excluded posts.",
      );
      onChanged?.(updated);
      // A pending queue shrinks after a save, so its next post occupies the same offset.
      // Saved-post views retain matching decisions, so advance those explicitly.
      const staysInView = status === "all" || status === decision.decision;
      if (staysInView && offset + 1 < (page.data?.total ?? 0)) setOffset(offset + 1);
      else setRevision((value) => value + 1);
    } catch (cause) {
      if (!mounted.current) return;
      setError(toError(cause));
      setSaveStatus("Not saved. Your choices are still here. Retry Save and next.");
    } finally {
      inFlight.current = false;
      if (mounted.current) setSaving(false);
    }
  };

  return (
    <section className="mx-auto flex max-w-4xl flex-col gap-5">
      <div>
        <p className="mb-2 text-xs font-semibold uppercase tracking-widest text-accent">
          Build your reviewed dataset
        </p>
        <h2 className="text-3xl font-semibold tracking-tight">Review &amp; label</h2>
        <p className="mt-2 max-w-2xl text-muted">
          Read each post once. Keep it and choose its sentiment, or exclude it with a reason. Each
          save moves you to the next review.
        </p>
        {collection?.query && (
          <p className="mt-3 break-words text-sm">
            <span className="text-muted">Your topic</span> · {collection.query}
          </p>
        )}
      </div>
      {collection && <CollectionStatus dataset={collection} busy={collectionLoading} message={collectionMessage} />}
      {page.error ? <p className="text-sm text-muted">Review totals could not be refreshed. Retry loading the review to see current counts.</p> : page.data && !page.loading && !collectionLoading ? (
        <ConsumerSummary counts={page.data.counts} timezone={policy.timezone ?? "UTC"} />
      ) : <div className="rounded-xl border border-rule p-5" role="group" aria-label="Loading review totals" aria-busy="true"><Skeleton className="w-2/3" /><div className="mt-4 grid grid-cols-3 gap-3"><Skeleton className="h-12" /><Skeleton className="h-12" /><Skeleton className="h-12" /></div></div>}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <label className="flex flex-col gap-1 text-sm font-medium">
          <span id={queueLabel}>Show posts</span>
          <select
            aria-labelledby={queueLabel}
            className="field min-h-11"
            value={status}
            disabled={locked}
            onChange={(event) => {
              setStatus(event.target.value);
              setOffset(0);
              setSaveStatus("");
            }}
          >
            <option value="needs_review">Needs review</option>
            <option value="all">All posts</option>
            <option value="include">Kept posts</option>
            <option value="exclude">Excluded posts — restore or correct</option>
          </select>
        </label>
        <p role="status" aria-live="polite" className="max-w-md text-sm text-muted">
          {collectionLoading
            ? "Collecting more posts…"
            : saveStatus ||
              (page.loading ? "Loading posts…" : "Save each review before moving on.")}
        </p>
      </div>
      <Notice error={error ?? page.error ?? collectionError} warnings={collection?.warnings} />
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
        <div className="flex items-center justify-between gap-3 border-b border-rule px-5 py-4 sm:px-7">
          <h3 ref={heading} tabIndex={-1} className="font-semibold">
            {item ? "Review this post" : "Review progress"}
          </h3>
          <span className="tnum text-sm text-muted">
            {page.error ? "Review queue unavailable" : page.loading || collectionLoading ? "Updating review queue…" : page.data?.total
              ? `${Math.min(offset + 1, page.data.total)} of ${page.data.total} in this view`
              : "No posts in this view"}
          </span>
        </div>
        <div className="flex flex-col gap-5 p-5 sm:p-7">
          {item && !page.loading && !collectionLoading && !page.error ? (
            <>
              <blockquote className="whitespace-pre-wrap break-words border-l-4 border-accent pl-5 text-lg leading-relaxed">
                {item.text}
              </blockquote>
              <details className="text-sm text-muted">
                <summary className="cursor-pointer">
                  Post details &amp; screening suggestion
                </summary>
                <div className="mt-3 flex flex-col gap-2 break-words">
                  <p>
                    Record {item.id} · Author {item.author_id ?? "unavailable"}
                  </p>
                  <p>
                    {item.created_at
                      ? new Date(item.created_at).toLocaleString(undefined, {
                          timeZone: policy.timezone,
                        })
                      : "Timestamp unavailable"}{" "}
                    ({policy.timezone})
                  </p>
                  <p>
                    Screening suggestion: {item.screening?.decision ?? "pending"}. Confirm relevance
                    against your topic; all sentiments are eligible.
                  </p>
                  <ul className="list-disc pl-5">
                    {item.screening?.evidence?.map((evidence) => (
                      <li key={evidence}>{evidence}</li>
                    ))}
                  </ul>
                  {item.screening?.duplicate_of && (
                    <p>
                      Possible duplicate of {item.screening.duplicate_of}. Originals are retained.
                    </p>
                  )}
                  {item.conversation_id && <p>Conversation: {item.conversation_id}</p>}
                  {!!item.references?.length && (
                    <p>
                      References:{" "}
                      {item.references
                        .map((reference) => `${reference.type}: ${reference.id}`)
                        .join("; ")}
                      . Related posts have not been fetched.
                    </p>
                  )}
                  {!!item.urls?.length && (
                    <ul>
                      {item.urls.map((url, index) => (
                        <li className="break-all" key={index}>
                          {url.expanded_url ?? url.url}
                        </li>
                      ))}
                    </ul>
                  )}
                  {!!item.eligibility_history?.length && (
                    <details>
                      <summary>Previous decisions</summary>
                      <ul>
                        {item.eligibility_history.map((review, index) => (
                          <li key={index}>
                            {review.reviewed_at}: {review.decision} · {review.reason} ·{" "}
                            {review.note}
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                </div>
              </details>
              {item.eligibility_reviewed && (
                <p className="text-sm text-muted">
                  Saved:{" "}
                  {item.eligibility === "include"
                    ? `kept${item.sentiment_reviewed ? ` · ${item.label}` : " · sentiment needed"}`
                    : "excluded"}
                  {item.eligibility === "include" && !page.data?.included_ids.includes(item.id)
                    ? " · held by author limit"
                    : ""}
                  .
                </p>
              )}
              <ReviewForm
                key={`${item.id}-${revision}`}
                item={item}
                busy={busy}
                onDirtyChange={setDirty}
                onSave={(decision) => void save(decision)}
              />
            </>
          ) : !page.loading && !collectionLoading && !page.error ? (
            <div className="py-6 text-center">
              <p className="text-xl font-semibold">
                {status === "needs_review" ? "You’re caught up" : "No posts in this view"}
              </p>
              <p className="mt-2 text-sm text-muted">
                {status === "needs_review"
                  ? "Your reviews are saved. Continue to Clean, or collect more posts if you’re short of your target."
                  : "Choose another view to review or correct a saved decision."}
              </p>
            </div>
          ) : (
            <div className="py-6"><p role="status" className="mb-5 text-sm text-muted">{page.error ? "The review could not be loaded. Retry to continue." : "Loading your review queue…"}</p>{!page.error && <><SkeletonLines lines={4} /><div className="mt-6 grid grid-cols-2 gap-3"><Skeleton className="h-20" /><Skeleton className="h-20" /></div></>}</div>
          )}
          <div className="flex justify-between gap-3 border-t border-rule pt-4">
            <button
              type="button"
              className="btn"
              disabled={locked || offset === 0}
              onClick={() => {
                setOffset(offset - 1);
                setSaveStatus("");
              }}
            >
              ← Previous
            </button>
            <button
              type="button"
              className="btn"
              disabled={locked || offset + 1 >= (page.data?.total ?? 0)}
              onClick={() => {
                setOffset(offset + 1);
                setSaveStatus("");
              }}
            >
              Next without saving →
            </button>
          </div>
        </div>
      </div>
      {collection && page.data && !page.error && onAdditional && (
        <AdditionalCandidates
          dataset={collection}
          counts={page.data.counts}
          busy={locked || !!page.error}
          costPerRead={costPerRead}
          onRequest={onAdditional}
        />
      )}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <button type="button" className="btn" disabled={locked} onClick={onBack}>
          ← Back to Collect
        </button>
        <button
          type="button"
          className="btn-primary"
          disabled={locked || !page.data || !!page.error}
          onClick={onContinue}
        >
          Continue to Clean →
        </button>
      </div>
      <p className="text-xs text-muted">
        Excluded posts stay recoverable. Only kept, fully reviewed posts within the author limit
        count toward your target. You can continue with a partial dataset.
      </p>
    </section>
  );
}

function ReviewForm({
  item,
  busy,
  onDirtyChange,
  onSave,
}: {
  item: PostRecord;
  busy: boolean;
  onDirtyChange: (dirty: boolean) => void;
  onSave: (decision: EligibilityItem) => void;
}) {
  const initialDecision =
    item.eligibility_reviewed && item.eligibility !== "pending" ? item.eligibility : "";
  const initialLabel = item.sentiment_reviewed ? (item.label as SentimentLabel) : "";
  const [decision, setDecision] = useState(initialDecision);
  const [label, setLabel] = useState<SentimentLabel | "">(initialLabel);
  const [reason, setReason] = useState<EligibilityReason | "">(item.eligibility_reason ?? "");
  const [note, setNote] = useState(item.eligibility_note ?? "");
  const id = useId();
  const override = !!decision && item.screening && decision !== item.screening.decision;
  const dirty =
    decision !== initialDecision ||
    label !== initialLabel ||
    reason !== (item.eligibility_reason ?? "") ||
    note !== (item.eligibility_note ?? "");
  useEffect(() => {
    onDirtyChange(dirty);
  }, [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);
  const valid =
    !!decision && (decision === "include" ? !!label : !!reason) && (!override || !!note.trim());
  return (
    <form
      className="flex flex-col gap-5"
      onSubmit={(event) => {
        event.preventDefault();
        if (!busy && valid)
          onSave({
            id: item.id,
            decision: decision as "include" | "exclude",
            reason: decision === "exclude" ? (reason as EligibilityReason) : null,
            note,
            label: decision === "include" ? (label as SentimentLabel) : null,
          });
      }}
    >
      <fieldset disabled={busy}>
        <legend className="mb-2 text-sm font-semibold">
          Does this post belong in your dataset?
        </legend>
        <div className="grid gap-3 sm:grid-cols-2">
          {(
            [
              ["include", "Keep & label", "A relevant consumer reaction"],
              ["exclude", "Exclude post", "Leave it out of the reviewed export"],
            ] as const
          ).map(([value, title, description]) => (
            <label
              key={value}
              className={`flex cursor-pointer items-start gap-3 rounded-xl border p-4 ${decision === value ? "border-accent bg-accent-soft" : "border-rule"}`}
            >
              <input
                type="radio"
                className="mt-1 size-4 shrink-0 accent-accent"
                name={`${id}-decision`}
                value={value}
                checked={decision === value}
                onChange={() => setDecision(value)}
              />
              <span className="font-medium">
                {title}
                <span className="mt-1 block text-xs font-normal text-muted">{description}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>
      {decision === "include" && (
        <fieldset disabled={busy}>
          <legend className="mb-2 text-sm font-semibold">
            What sentiment does the post express?
          </legend>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {SENTIMENT_LABELS.map((value) => (
              <label
                key={value}
                className={`flex min-h-12 cursor-pointer items-center gap-2 rounded-xl border p-3 capitalize ${label === value ? "border-accent bg-accent-soft" : "border-rule"}`}
              >
                <input
                  type="radio"
                  className="size-4 accent-accent"
                  name={`${id}-label`}
                  checked={label === value}
                  onChange={() => setLabel(value)}
                />
                {value}
              </label>
            ))}
          </div>
          <p className="mt-2 text-xs text-muted">
            Positive and negative opinions are equally eligible. Mixed means both positive and
            negative sentiment.
          </p>
        </fieldset>
      )}
      {decision === "exclude" && (
        <label className="flex flex-col gap-1 text-sm font-medium">
          <span id={`${id}-reason-label`}>Exclusion reason</span>
          <select
            aria-labelledby={`${id}-reason-label`}
            className="field min-h-11"
            disabled={busy}
            required
            value={reason}
            onChange={(event) => setReason(event.target.value as EligibilityReason | "")}
          >
            <option value="">Choose a reason</option>
            {REASONS.map(([value, title]) => (
              <option key={value} value={value}>
                {title}
              </option>
            ))}
          </select>
        </label>
      )}
      {decision && (
        <label className="flex flex-col gap-1 text-sm font-medium">
          {override
            ? "Why override the screening suggestion? (required)"
            : "Review note (optional)"}
          <textarea
            className="field"
            rows={2}
            disabled={busy}
            required={!!override}
            maxLength={2000}
            value={note}
            onChange={(event) => setNote(event.target.value)}
          />
        </label>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <button type="submit" className="btn-primary min-h-11" disabled={busy || !valid}>
          Save and next →
        </button>
        {dirty && (
          <button
            type="button"
            className="btn min-h-11"
            disabled={busy}
            onClick={() => {
              setDecision(initialDecision);
              setLabel(initialLabel);
              setReason(item.eligibility_reason ?? "");
              setNote(item.eligibility_note ?? "");
            }}
          >
            Discard changes
          </button>
        )}
        <span className="text-xs text-muted">
          {dirty ? "Unsaved changes" : "Choose a decision to continue"}
        </span>
      </div>
    </form>
  );
}
