import { useCallback, useEffect, useId, useRef, useState } from "react";

import { api, isAbort } from "../../api/client";
import {
  SENTIMENT_LABELS,
  type LabelEstimate,
  type LabelSummary,
  type Record,
  type ReviewMode,
  type SampleUnit,
  type SentimentLabel,
} from "../../api/types";
import { useAsync } from "../../hooks/useAsync";
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

const PRICING_URL = "https://aws.amazon.com/comprehend/pricing/";

type Phase = "method" | "labelling" | "review-choice" | "reviewing" | "summary";
const SLICE = 250;
const FLUSH_EVERY = 10;
const KEYS: { [key: string]: SentimentLabel } = { "1": "positive", p: "positive", "2": "negative", n: "negative", "3": "neutral", u: "neutral", "4": "mixed", m: "mixed" };
const usd = (n: number) => `$${n.toFixed(n < 0.01 && n > 0 ? 4 : 2)}`;

/**
 * Stage 4. Comprehend labels everything cheaply; a person reviews some or all of it. Every paid
 * slice is saved server-side before the next starts, and reviewer decisions are flushed in
 * small batches, so a crash or a closed tab never loses more than a few seconds of work.
 */
export function LabelStep({ datasetId, diagnostics, comprehendEnabled, checkpointLocation, onBack, onContinue }: Props) {
  const summary = useAsync<LabelSummary>();
  const estimate = useAsync<LabelEstimate>();
  const [phase, setPhase] = useState<Phase>("method");
  const [error, setError] = useState<Error | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [progress, setProgress] = useState<{ done: number; total: number; spent: number | null } | null>(null);
  const labelling = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    await Promise.all([summary.run((s) => api.labelSummary(datasetId, s)), estimate.run((s) => api.labelEstimate(datasetId, s))]);
  }, [datasetId, summary.run, estimate.run]);

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
      if (!isAbort(e)) setError(e as Error);
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
        } catch (e) { setError(e as Error); }
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

/** The estimate, or an honest "unavailable" when no current rate could be fetched. */
function CostChip({ est }: { est: LabelEstimate }) {
  if (est.estimated_cost_usd == null) {
    return (
      <div className="rounded-lg bg-warn-bg px-3 py-2 text-sm text-warn-ink">
        <strong className="font-semibold">Estimate unavailable right now.</strong> Check{" "}
        <a className="underline" href={PRICING_URL} target="_blank" rel="noreferrer">AWS's current Comprehend pricing</a>{" "}
        and estimate the cost manually before proceeding: this job is {est.billable_units.toLocaleString()} billable units for {est.records_to_send.toLocaleString()} posts.
      </div>
    );
  }
  const freshness = est.price_status === "live" ? "live price" : est.price_status === "cached" ? "price cached today" : "price may be out of date";
  return (
    <div className="tnum inline-flex w-fit flex-wrap items-baseline gap-2 rounded-full border border-rule bg-surface-2 px-3 py-1.5 text-sm">
      <span className="text-muted">Estimated cost</span><strong>{usd(est.estimated_cost_usd)}</strong>
      <span className="text-xs text-muted">{est.records_to_send.toLocaleString()} posts · {est.billable_units.toLocaleString()} units · ${est.cost_per_unit_usd}/unit · {freshness}</span>
    </div>
  );
}

function ConfirmDialog({ est, onCancel, onConfirm }: { est: LabelEstimate; onCancel: () => void; onConfirm: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { ref.current?.showModal(); }, []);
  return (
    <dialog ref={ref} onClose={onCancel} aria-labelledby="confirm-heading" className="glass-panel m-auto w-[min(480px,92vw)] p-6 text-ink shadow-2xl backdrop:bg-transparent">
      <h3 id="confirm-heading" className="font-semibold">Send {est.records_to_send.toLocaleString()} posts to Amazon Comprehend?</h3>
      <dl className="tnum mt-4 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
        <dt className="text-muted">Billable units</dt><dd>{est.billable_units.toLocaleString()}</dd>
        <dt className="text-muted">Rate</dt><dd>{est.cost_per_unit_usd != null ? `$${est.cost_per_unit_usd} per unit (${est.unit_chars} chars, min ${est.min_units_per_document})` : "unavailable right now"}</dd>
        <dt className="text-muted">Estimated charge</dt><dd className="text-lg font-semibold">{est.estimated_cost_usd != null ? usd(est.estimated_cost_usd) : "—"}</dd>
      </dl>
      {est.estimated_cost_usd == null && (
        <p className="mt-3 text-sm text-warn-ink">No current price could be fetched. Check <a className="underline" href={PRICING_URL} target="_blank" rel="noreferrer">AWS's Comprehend pricing</a> and estimate the charge yourself ({est.billable_units.toLocaleString()} units) before confirming.</p>
      )}
      <p className="mt-3 text-xs text-muted">Charged to the AWS account the server runs under. Labels are saved after every batch of {SLICE}; you can cancel at any time and keep what has been labelled.</p>
      <div className="mt-5 flex justify-end gap-2"><button type="button" className="btn" onClick={onCancel}>Cancel</button><button type="button" className="btn-primary" onClick={onConfirm}>Confirm and label</button></div>
    </dialog>
  );
}

function ReviewChoice({ total, onChoose, onBack }: { total: number; onChoose: (m: ReviewMode, size: number, unit: SampleUnit) => void; onBack: () => void }) {
  const [mode, setMode] = useState<ReviewMode>("sample");
  const [unit, setUnit] = useState<SampleUnit>("count");
  // Held as a string so the field can be empty while typing; "0100" cannot happen because the
  // value is replaced, never concatenated, and is normalised on every change.
  const [raw, setRaw] = useState("150");
  const id = useId();

  const typed = Number.parseInt(raw, 10);
  const valid = Number.isFinite(typed) && typed >= 1 && (unit === "count" ? typed <= total : typed <= 100);
  // Both readings of the same choice, always computed against the dataset total.
  const posts = !valid ? 0 : unit === "count" ? typed : Math.max(1, Math.round((total * typed) / 100));
  const percent = total === 0 ? 0 : Math.round((posts / total) * 100);
  const effective = mode === "all" ? total : mode === "none" ? 0 : posts;
  const max = unit === "count" ? total : 100;

  /** Keep the number readable: strip leading zeros, clamp to range, allow an empty field. */
  const setSize = (next: string) => {
    const digits = next.replace(/[^0-9]/g, "").replace(/^0+(?=\d)/, "");
    if (digits === "") return setRaw("");
    setRaw(String(Math.min(Number.parseInt(digits, 10), max)));
  };

  /** Switching unit keeps the same *number of posts*, so the choice does not silently change. */
  const switchUnit = (next: SampleUnit) => {
    if (next === unit) return;
    if (valid) setRaw(String(next === "percent" ? Math.max(1, percent) : posts));
    setUnit(next);
  };

  return (
    <div className="glass-panel flex flex-col gap-4 p-5">
      <h3 className="font-semibold">How much will you review by hand?</h3>
      <div className="grid gap-2 sm:grid-cols-3">
        {([["none", "Skip review", "Keep Comprehend's labels as they are."], ["sample", "Review a sample", "Enough to measure how often you disagree."], ["all", "Review everything", "Every post gets a human label."]] as [ReviewMode, string, string][]).map(([m, t, d]) => (
          <label key={m} data-selected={mode === m} className="selectable flex cursor-pointer flex-col gap-1 p-3">
            <span className="flex items-center gap-2 font-medium"><input type="radio" name="review-mode" className="accent-accent" checked={mode === m} onChange={() => setMode(m)} />{t}</span>
            <span className="text-xs text-muted">{d}</span>
          </label>
        ))}
      </div>
      {mode === "sample" && (
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex flex-col gap-1">
              <label htmlFor={id} className="text-sm text-muted">Sample size</label>
              <input
                id={id} type="text" inputMode="numeric" autoComplete="off"
                aria-label={unit === "count" ? "Sample size in posts" : "Sample size as a percent"}
                aria-describedby={`${id}-help`} aria-invalid={!valid}
                value={raw} onChange={(e) => setSize(e.target.value)}
                className="field tnum w-28"
              />
            </div>
            <div role="group" aria-label="Sample size unit" className="grid grid-cols-2 gap-2 text-sm">
              {(["count", "percent"] as SampleUnit[]).map((u) => (
                <button key={u} type="button" aria-pressed={unit === u} onClick={() => switchUnit(u)} className="selectable px-3 py-2">
                  {u === "count" ? "posts" : "%"}
                </button>
              ))}
            </div>
          </div>
          <p id={`${id}-help`} className="text-sm text-muted">
            {valid ? (
              <>
                <span className="tnum font-medium text-ink">{posts.toLocaleString()} posts</span>{" "}
                <span className="tnum">({percent}% of {total.toLocaleString()})</span>, chosen at random.
                {" "}100–150 is enough to estimate agreement within a few points.
              </>
            ) : (
              <span className="text-warn-ink">Enter a number between 1 and {max.toLocaleString()}{unit === "percent" ? "%" : " posts"}.</span>
            )}
          </p>
        </div>
      )}
      <div className="flex flex-wrap justify-between gap-3 border-t border-rule pt-4">
        <button type="button" className="btn" onClick={onBack}>← Back</button>
        <button type="button" className="btn-primary" disabled={mode === "sample" && !valid} onClick={() => onChoose(mode, mode === "sample" ? posts : 1, "count")}>
          {mode === "none" ? "Skip review" : `Start reviewing ${effective.toLocaleString()} posts`}
        </button>
      </div>
    </div>
  );
}

function Reviewer({ datasetId, onError, onDone }: { datasetId: string; onError: (e: Error) => void; onDone: () => void }) {
  const [items, setItems] = useState<Record[]>([]);
  const [total, setTotal] = useState(0);
  const [index, setIndex] = useState(0);
  const [decisions, setDecisions] = useState<{ [id: string]: SentimentLabel }>({});
  const pending = useRef<{ id: string; label: SentimentLabel }[]>([]);
  const [agree, setAgree] = useState({ agreed: 0, compared: 0 });
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.reviewPage(datasetId, 0, 200).then((p) => { setItems(p.items); setTotal(p.total); }).catch(onError);
  }, [datasetId, onError]);

  useEffect(() => {
    if (index >= items.length && items.length < total) {
      api.reviewPage(datasetId, items.length, 200).then((p) => setItems((prev) => [...prev, ...p.items])).catch(onError);
    }
  }, [index, items.length, total, datasetId, onError]);

  const flush = useCallback(async () => {
    if (pending.current.length === 0) return;
    const batch = pending.current; pending.current = [];
    setSaving(true);
    try { await api.manualLabels(datasetId, batch); } catch (e) { pending.current = [...batch, ...pending.current]; onError(e as Error); } finally { setSaving(false); }
  }, [datasetId, onError]);

  const decide = useCallback((label: SentimentLabel) => {
    const item = items[index]; if (!item) return;
    setDecisions((d) => ({ ...d, [item.id]: label }));
    if (item.comprehend_label) setAgree((a) => ({ agreed: a.agreed + (item.comprehend_label === label ? 1 : 0), compared: a.compared + 1 }));
    pending.current.push({ id: item.id, label });
    if (pending.current.length >= FLUSH_EVERY) void flush();
    setIndex((i) => i + 1);
  }, [items, index, flush]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { const l = KEYS[e.key.toLowerCase()]; if (l && !e.metaKey && !e.ctrlKey) { e.preventDefault(); decide(l); } if (e.key === "ArrowLeft") setIndex((i) => Math.max(0, i - 1)); };
    window.addEventListener("keydown", onKey); return () => window.removeEventListener("keydown", onKey);
  }, [decide]);

  useEffect(() => () => { void flush(); }, [flush]);

  const finish = async () => { await flush(); onDone(); };
  const item = items[index];
  const finished = index >= total && total > 0;

  return (
    <div className="glass-panel flex flex-col gap-4 p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-semibold">Review</h3>
        <span className="tnum text-sm text-muted">{Math.min(index, total).toLocaleString()} of {total.toLocaleString()}{agree.compared > 0 && ` · agree with Comprehend ${Math.round((100 * agree.agreed) / agree.compared)}%`}{saving && " · saving…"}</span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded bg-surface-2"><div className="h-full bg-accent" style={{ width: `${total ? (100 * Math.min(index, total)) / total : 0}%` }} /></div>

      {!finished && !item && <SkeletonLines lines={3} />}
      {!finished && item && (
        <>
          <blockquote className="rounded-lg bg-surface-2 p-4 text-lg leading-relaxed">{item.text}</blockquote>
          <p className="flex items-start gap-1.5 text-xs text-muted">
            <InfoIcon />
            <span>Showing the original text for review — labels apply to the processed version used in Task 2.</span>
          </p>
          <div className="flex flex-wrap items-center gap-2 text-sm text-muted">
            {item.comprehend_label ? <span>Comprehend says <strong className="text-ink">{item.comprehend_label}</strong>{item.comprehend_confidence != null && <span className="tnum"> ({Math.round(item.comprehend_confidence * 100)}%)</span>}</span> : <span>No Comprehend label for this post.</span>}
            {decisions[item.id] && <span>· you said <strong className="text-ink">{decisions[item.id]}</strong></span>}
          </div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {SENTIMENT_LABELS.map((l, i) => (
              <button key={l} type="button" onClick={() => decide(l)} aria-pressed={decisions[item.id] === l} className="selectable flex items-center justify-between px-3.5 py-3 text-sm font-medium">
                <span className="capitalize">{l}{item.comprehend_label === l && decisions[item.id] !== l && <span className="ml-1 text-xs font-normal text-muted">(Comprehend)</span>}</span><kbd className="rounded bg-surface-2 px-1.5 text-xs text-muted">{i + 1}</kbd>
              </button>
            ))}
          </div>
          <p className="text-xs text-muted">Keys 1–4 or P / N / U / M. ← goes back one. Labels save every {FLUSH_EVERY} decisions and when you finish.</p>
        </>
      )}
      {finished && <p className="text-sm">All {total.toLocaleString()} reviewed.</p>}
      <div className="flex flex-wrap justify-between gap-3 border-t border-rule pt-4">
        <button type="button" className="btn" disabled={index === 0} onClick={() => setIndex((i) => i - 1)}>← Previous</button>
        <button type="button" className="btn-primary" onClick={() => void finish()}>{finished ? "Finish" : "Stop here and keep labels"}</button>
      </div>
    </div>
  );
}

const LABEL_BAR: { [label: string]: string } = {
  positive: "var(--color-introduced)",
  negative: "var(--color-removed)",
  neutral: "var(--color-muted)",
  mixed: "var(--color-accent)",
};

/** Distribution and provenance as two labelled sections, with a stacked bar for the split. */
function SummaryBlock({ s }: { s: LabelSummary }) {
  const pct = (n: number) => (s.total ? (100 * n) / s.total : 0);
  const distribution = Object.entries(s.by_label).sort((a, b) => b[1] - a[1]);
  const provenance = Object.entries(s.by_source).sort((a, b) => b[1] - a[1]);

  const rows = (entries: [string, number][], total: number) => (
    <ul className="mt-2 flex flex-col divide-y divide-rule text-sm">
      {entries.map(([name, count]) => (
        <li key={name} className="flex items-baseline justify-between gap-4 py-1.5">
          <span className="capitalize">{name}</span>
          <span className="tnum tabular-nums text-right">
            {count.toLocaleString()}
            <span className="ml-2 inline-block w-12 text-muted">
              {total ? `${((100 * count) / total).toFixed(0)}%` : ""}
            </span>
          </span>
        </li>
      ))}
    </ul>
  );

  return (
    <div className="flex flex-col gap-5">
      <section aria-labelledby="dist-heading">
        <h4 id="dist-heading" className="text-xs font-semibold uppercase tracking-wide text-muted">Distribution</h4>
        <div className="mt-2 flex h-3 w-full overflow-hidden rounded-full border border-rule" role="img"
          aria-label={distribution.map(([k, v]) => `${k} ${Math.round(pct(v))}%`).join(", ")}>
          {distribution.map(([label, count]) => (
            <span key={label} title={`${label}: ${count.toLocaleString()} (${Math.round(pct(count))}%)`}
              style={{ width: `${pct(count)}%`, background: LABEL_BAR[label] ?? "var(--color-accent)" }} />
          ))}
        </div>
        {rows(distribution, s.total)}
      </section>

      <section aria-labelledby="prov-heading" className="border-t border-rule pt-4">
        <h4 id="prov-heading" className="text-xs font-semibold uppercase tracking-wide text-muted">Provenance</h4>
        {rows(provenance, s.total)}
        {s.manual_vs_comprehend_agreement != null && (
          <p className="mt-3 text-sm">
            Reviewers agreed with Comprehend on{" "}
            <strong className="tnum">{(s.manual_vs_comprehend_agreement * 100).toFixed(1)}%</strong>{" "}
            of {s.reviewed.toLocaleString()} reviewed posts ({s.disagreements} disagreements). Quote this in your write-up.
          </p>
        )}
      </section>
    </div>
  );
}

function InfoIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
      className="mt-0.5 shrink-0" aria-hidden="true">
      <circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 7.5h.01" strokeLinecap="round" />
    </svg>
  );
}
