import { useCallback, useEffect, useId, useRef, useState } from "react";

import { api } from "../../api/client";
import {
  SENTIMENT_LABELS,
  type ConsumerPolicy,
  type DatasetSummary,
  type EligibilityDecision,
  type EligibilityItem,
  type EligibilityPage,
  type EligibilityReason,
  type PostRecord,
  type SentimentLabel,
} from "../../api/types";
import { toError, useAsync } from "../../hooks/useAsync";
import { Notice } from "../ui/Notice";
import { ConsumerSummary } from "./ConsumerSummary";
import { AdditionalCandidates } from "../collect/AdditionalCandidates";

interface Props {
  datasetId: string;
  policy: ConsumerPolicy;
  onChanged?: () => void;
  onReviewActiveChange?: (active: boolean) => void;
  onBack: () => void;
  onContinue: () => void;
  onCollectMore?: () => void;
  collection?: DatasetSummary;
  collectionLoading?: boolean;
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

/** Eligibility is reviewed on originals, before any automated sentiment is displayed. */
export function ConsumerReviewer({
  datasetId,
  policy,
  onChanged,
  onReviewActiveChange,
  onBack,
  onContinue,
  onCollectMore,
  collection,
  collectionLoading = false,
  costPerRead,
  onAdditional,
}: Props) {
  const page = useAsync<EligibilityPage>();
  const { run: loadPage, cancel } = page;
  const [status, setStatus] = useState("all");
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [saving, setSaving] = useState(false);
  const inFlight = useRef(false);
  const [saveStatus, setSaveStatus] = useState("");
  const [error, setError] = useState<Error | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const reviewRegion = useRef<HTMLDivElement>(null);

  useEffect(() => {
    void loadPage((signal) => api.eligibilityPage(datasetId, offset, status, signal));
    return cancel;
  }, [datasetId, offset, status, revision, loadPage, cancel]);
  useEffect(() => {
    heading.current?.focus();
  }, [offset, status]);
  useEffect(() => {
    onReviewActiveChange?.(saving);
    return () => onReviewActiveChange?.(false);
  }, [saving, onReviewActiveChange]);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (inFlight.current) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, []);

  const save = useCallback(
    async (operation: () => Promise<unknown>) => {
      if (inFlight.current) return;
      inFlight.current = true;
      setSaving(true);
      setError(null);
      setSaveStatus("Saving…");
      try {
        await operation();
        setSaveStatus("Decision saved.");
        setRevision((v) => v + 1);
        onChanged?.();
      } catch (cause) {
        setError(toError(cause));
        setSaveStatus("Not saved. Your choices remain below; retry Save.");
      } finally {
        inFlight.current = false;
        setSaving(false);
      }
    },
    [onChanged],
  );
  const item = page.data?.items[0];
  const busy = saving || page.loading || collectionLoading;
  const sentiment = status === "sentiment";
  const changeSentiment = useCallback(
    (label: SentimentLabel) => {
      if (!item || busy) return;
      void save(() => api.manualLabels(datasetId, [{ id: item.id, label }]));
    },
    [item, busy, datasetId, save],
  );
  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      if (
        !sentiment ||
        busy ||
        !(event.target instanceof HTMLElement) ||
        !reviewRegion.current?.contains(event.target)
      )
        return;
      if (
        event.ctrlKey ||
        event.metaKey ||
        event.altKey ||
        event.shiftKey ||
        event.repeat ||
        event.target.matches("input, select, textarea, [contenteditable=true]")
      )
        return;
      const label = SENTIMENT_LABELS[Number(event.key) - 1];
      if (label) {
        event.preventDefault();
        changeSentiment(label);
      }
    };
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, [sentiment, busy, changeSentiment]);

  return (
    <section className="mx-auto flex max-w-4xl flex-col gap-5">
      <h2 className="text-2xl font-semibold">Review consumer reactions</h2>
      {collection?.query && (
        <p className="break-words text-sm">Collection topic / query: {collection.query}</p>
      )}
      <p className="text-sm text-muted">
        First decide whether a post belongs in this dataset. Negative opinions, disappointment and
        sarcasm use the same eligibility rules as positive reactions. Then review sentiment for
        included posts.
      </p>
      <p className="text-sm text-muted">
        To drop an unwanted record from the reviewed export, choose an eligibility queue below,
        select Exclude, choose a reason, and save. Originals stay in Human exclusions so you can
        undo a decision. Confirm topic relevance against the collection query; screening suggestions
        do not establish it.
      </p>
      {page.data && (
        <ConsumerSummary counts={page.data.counts} timezone={policy.timezone ?? "UTC"} />
      )}
      <label className="flex flex-col gap-1 text-sm">
        Review queue
        <select
          className="field"
          value={status}
          disabled={busy}
          onChange={(event) => {
            setStatus(event.target.value);
            setOffset(0);
            setSaveStatus("");
          }}
        >
          <option value="all">All candidates — eligibility first</option>
          <option value="pending">Pending eligibility</option>
          <option value="include">Human inclusions (including author-limit holds)</option>
          <option value="exclude">Human exclusions — inspect or correct</option>
          <option value="sentiment">Sentiment — included after author limit</option>
        </select>
      </label>
      <Notice error={error ?? page.error} />
      {page.error && (
        <button type="button" className="btn" onClick={() => setRevision((v) => v + 1)}>
          Retry loading review
        </button>
      )}
      <p role="status" className="text-sm">
        {saveStatus ||
          (page.loading
            ? "Loading…"
            : "Decisions save when you confirm. Previous and Next do not assign decisions.")}
      </p>
      <div
        ref={reviewRegion}
        className="glass-panel flex flex-col gap-4 p-5"
        role="group"
        aria-label="Consumer review"
      >
        <h3 ref={heading} tabIndex={-1} className="font-semibold">
          {sentiment
            ? "What sentiment does this included post express?"
            : "Does this post belong in this consumer-reaction dataset?"}
        </h3>
        {item && !page.loading ? (
          <>
            <p className="text-xs text-muted">
              Record {item.id} ·{" "}
              {item.created_at
                ? new Date(item.created_at).toLocaleString(undefined, { timeZone: policy.timezone })
                : "Timestamp unavailable"}{" "}
              ({policy.timezone}) · Author {item.author_id ?? "unavailable"}
            </p>
            <blockquote className="whitespace-pre-wrap break-words rounded bg-surface-2 p-4 leading-relaxed">
              {item.text}
            </blockquote>
            {item.conversation_id && (
              <p className="text-xs text-muted">Conversation: {item.conversation_id}</p>
            )}
            {!!item.references?.length && (
              <p className="text-xs text-muted">
                References: {item.references.map((ref) => `${ref.type}: ${ref.id}`).join("; ")}.
                Related content has not been fetched.
              </p>
            )}
            {!!item.urls?.length && (
              <details className="text-xs text-muted">
                <summary>Available URL metadata (no linked pages fetched)</summary>
                <ul>
                  {item.urls.map((url, i) => (
                    <li className="break-all" key={`${url.url}-${i}`}>
                      {url.expanded_url ?? url.url}
                    </li>
                  ))}
                </ul>
              </details>
            )}
            {sentiment ? (
              <>
                <p className="text-sm">
                  {item.sentiment_reviewed
                    ? `Saved human sentiment: ${item.label}`
                    : "Sentiment review pending"}
                  .{" "}
                  {item.comprehend_label
                    ? `Original automated suggestion: ${item.comprehend_label}${item.comprehend_confidence == null ? "" : ` (${Math.round(item.comprehend_confidence * 100)}%)`}.`
                    : "No automated sentiment suggestion."}
                </p>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                  {SENTIMENT_LABELS.map((label, index) => (
                    <button
                      type="button"
                      className="selectable p-3 capitalize"
                      key={label}
                      disabled={busy}
                      aria-pressed={item.sentiment_reviewed && item.label === label}
                      onClick={() => changeSentiment(label)}
                    >
                      {label} ({index + 1})
                    </button>
                  ))}
                </div>
                <p className="text-xs text-muted">
                  Choosing the same label confirms it and saves a completed review. Keyboard: 1–4
                  within this review.
                </p>
              </>
            ) : (
              <>
                <p className="text-sm">
                  Screening suggestion: <strong>{item.screening?.decision ?? "pending"}</strong>
                  {item.screening?.reason && ` · ${item.screening.reason}`}
                </p>
                <ul className="list-disc pl-5 text-sm text-muted">
                  {item.screening?.evidence?.map((evidence) => (
                    <li key={evidence}>{evidence}</li>
                  ))}
                </ul>
                {item.screening?.duplicate_of && (
                  <p className="text-xs">
                    Duplicate group reference: {item.screening.duplicate_of}. Originals are
                    preserved.
                  </p>
                )}
                <p className="text-sm">
                  Human eligibility: {item.eligibility_reviewed ? item.eligibility : "pending"}
                  {item.eligibility === "include" && !page.data?.included_ids.includes(item.id)
                    ? " · held by author limit"
                    : ""}
                </p>
                <EligibilityForm
                  key={`${item.id}-${revision}`}
                  item={item}
                  busy={busy}
                  onSave={(decision) =>
                    void save(() => api.reviewEligibility(datasetId, [decision]))
                  }
                />
                {!!item.eligibility_history?.length && (
                  <details className="text-xs">
                    <summary>Previous eligibility decisions</summary>
                    <ul>
                      {item.eligibility_history.map((review, index) => (
                        <li key={index}>
                          {review.reviewed_at}: {review.decision} · {review.reason} · {review.note}
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
              </>
            )}
          </>
        ) : (
          !page.loading && (
            <p>No records in this queue at this position. Choose another queue or go back.</p>
          )
        )}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <button
            type="button"
            className="btn"
            disabled={busy || offset === 0}
            onClick={() => {
              setOffset((v) => v - 1);
              setSaveStatus("");
            }}
          >
            ← Previous
          </button>
          <span className="tnum text-sm">
            {page.data?.total ? Math.min(offset + 1, page.data.total) : 0} of{" "}
            {page.data?.total ?? 0}
          </span>
          <button
            type="button"
            className="btn"
            disabled={busy || offset + 1 >= (page.data?.total ?? 0)}
            onClick={() => {
              setOffset((v) => v + 1);
              setSaveStatus("");
            }}
          >
            Next →
          </button>
        </div>
      </div>
      {collection && page.data && onAdditional && (
        <AdditionalCandidates
          dataset={collection}
          counts={page.data.counts}
          busy={busy || !!page.error}
          costPerRead={costPerRead}
          onRequest={onAdditional}
        />
      )}
      <div className="flex flex-wrap justify-between gap-3">
        <button type="button" className="btn" disabled={busy} onClick={onBack}>
          ← Back to Analyze
        </button>
        {onCollectMore && (
          <button type="button" className="btn" disabled={busy} onClick={onCollectMore}>
            Review collection / collect more
          </button>
        )}
        <button
          type="button"
          className="btn-primary"
          disabled={busy || !page.data}
          onClick={onContinue}
        >
          Continue to Export →
        </button>
      </div>
    </section>
  );
}

function EligibilityForm({
  item,
  busy,
  onSave,
}: {
  item: PostRecord;
  busy: boolean;
  onSave: (decision: EligibilityItem) => void;
}) {
  const [decision, setDecision] = useState<EligibilityDecision>(
    item.eligibility_reviewed
      ? (item.eligibility ?? "pending")
      : (item.screening?.decision ?? "pending"),
  );
  const [reason, setReason] = useState<EligibilityReason | "">(
    item.eligibility_reason ?? item.screening?.reason ?? "",
  );
  const [note, setNote] = useState(item.eligibility_note ?? "");
  const id = useId();
  const override = decision !== "pending" && item.screening && decision !== item.screening.decision;
  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (!busy)
          onSave({
            id: item.id,
            decision,
            reason: decision === "include" ? null : reason || null,
            note,
          });
      }}
    >
      <label className="flex flex-col gap-1 text-sm">
        Eligibility decision
        <select
          className="field"
          disabled={busy}
          value={decision}
          onChange={(event) => setDecision(event.target.value as EligibilityDecision)}
        >
          <option value="include">Include</option>
          <option value="exclude">Exclude</option>
          <option value="pending">Needs review / pending</option>
        </select>
      </label>
      {decision !== "include" && (
        <label className="flex flex-col gap-1 text-sm">
          Eligibility reason
          <select
            className="field"
            disabled={busy}
            required={decision === "exclude"}
            value={reason}
            onChange={(event) => setReason(event.target.value as EligibilityReason | "")}
          >
            <option value="">Choose a reason</option>
            {REASONS.map(([code, label]) => (
              <option key={code} value={code}>
                {label}
              </option>
            ))}
          </select>
        </label>
      )}
      <label className="flex flex-col gap-1 text-sm" htmlFor={id}>
        Decision note {override ? "(required for this override)" : "(optional)"}
      </label>
      <textarea
        id={id}
        className="field"
        disabled={busy}
        required={!!override}
        maxLength={2000}
        value={note}
        onChange={(event) => setNote(event.target.value)}
      />
      <button
        type="submit"
        className="btn-primary self-start"
        disabled={busy || (decision === "exclude" && !reason) || (!!override && !note.trim())}
      >
        Save eligibility decision
      </button>
      <p className="text-xs text-muted">
        Confirming an unchanged suggestion still saves human review. To undo a decision, choose
        Needs review and save; you can correct exclusions here at any time.
      </p>
    </form>
  );
}
