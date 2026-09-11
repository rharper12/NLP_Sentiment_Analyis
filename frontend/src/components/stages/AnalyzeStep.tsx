import { Suspense, lazy, useEffect, useState } from "react";

import { api } from "../../api/client";
import type { PreprocessResponse, RecordPair } from "../../api/types";
import type { Theme } from "../../hooks/useTheme";
import { useAsync } from "../../hooks/useAsync";
import { RecordsGrid } from "../RecordsGrid";
import { SkeletonLines } from "../ui/Skeleton";
import { StepWaterfall } from "../StepWaterfall";
import { Notice } from "../ui/Notice";
import { Skeleton } from "../ui/Skeleton";

const DiffDialog = lazy(() => import("../DiffDialog"));

interface Props {
  datasetId: string;
  recordCount: number;
  theme: Theme;
  run: PreprocessResponse | null;
  busy: boolean;
  error: Error | null;
  runVersion: number;
  onCancel: () => void;
  onRerun: () => void;
  onBack: () => void;
  onContinue: () => void;
}

const fmt = (n: number) => n.toLocaleString();
const pct = (a: number, b: number) => (a === 0 ? null : ((b - a) / a) * 100);

/** Stage 3. The numbers first, then a chart, then the prose, then the evidence (records). */
export function AnalyzeStep({ datasetId, recordCount, theme, run, busy, error, runVersion, onCancel, onRerun, onBack, onContinue }: Props) {
  const [selected, setSelected] = useState<RecordPair | null>(null);
  const records = useAsync<RecordPair[]>();
  const [copied, setCopied] = useState(false);
  const warnings = run?.report.warnings.filter((w) => !w.includes("disabled")) ?? [];
  const disabledNote = run?.report.warnings.filter((w) => w.includes("disabled")) ?? [];

  const rows = run ? [
    { label: "Posts", a: run.metrics_before.record_count, b: run.metrics_after.record_count, f: fmt },
    { label: "Vocabulary", a: run.metrics_before.vocab_size, b: run.metrics_after.vocab_size, f: fmt, help: "unique tokens the model must learn" },
    { label: "Tokens per post", a: run.metrics_before.avg_tokens, b: run.metrics_after.avg_tokens, f: (n: number) => n.toFixed(1) },
    { label: "Type–token ratio", a: run.metrics_before.type_token_ratio, b: run.metrics_after.type_token_ratio, f: (n: number) => n.toFixed(3), help: "vocabulary ÷ total tokens; higher is sparser" },
  ] : [];

  // Reload whenever a pipeline run finishes so the processed column reflects the latest run.
  useEffect(() => {
    void records.run((signal) => api.allRecords(datasetId, recordCount, signal));
  }, [datasetId, recordCount, runVersion, records.run]);

  const copy = async () => { if (run?.report.explanation) { await navigator.clipboard.writeText(run.report.explanation); setCopied(true); setTimeout(() => setCopied(false), 1500); } };

  return (
    <section className="mx-auto flex max-w-5xl flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight">What changed</h2>
          <p className="mt-1 text-muted">Before → after for the whole dataset, then step by step, then post by post.</p>
        </div>
        <div className="flex gap-2">
          <button type="button" className="btn" onClick={onBack}>← Adjust steps</button>
          {busy ? <button type="button" className="btn" onClick={onCancel}>Cancel</button> : <button type="button" className="btn" onClick={onRerun}>Run again</button>}
        </div>
      </div>

      <Notice error={error} warnings={warnings} />

      <div className="grid gap-6 lg:grid-cols-2">
        <div className="glass-panel p-5">
          <h3 className="mb-3 font-semibold">Dataset</h3>
          <table className="tnum w-full text-sm">
            <tbody>
              {busy && rows.length === 0 && Array.from({ length: 4 }, (_, i) => <tr key={i}><td className="py-2 text-muted"><Skeleton className="w-24" /></td><td className="py-2 text-right"><Skeleton className="w-12" /></td><td className="w-8" /><td className="py-2 text-right"><Skeleton className="w-14" /></td><td className="py-2 pl-3 text-right"><Skeleton className="w-10" /></td></tr>)}
              {rows.map((r) => { const d = pct(r.a, r.b); return (
                <tr key={r.label} className="border-t border-rule first:border-0">
                  <th scope="row" className="py-2 pr-2 text-left font-medium text-muted">{r.label}{r.help && <span className="block text-xs font-normal">{r.help}</span>}</th>
                  <td className="py-2 text-right">{r.f(r.a)}</td>
                  <td className="w-8 text-center text-muted">→</td>
                  <td className="py-2 text-right text-lg font-semibold">{r.f(r.b)}</td>
                  <td className={`py-2 pl-3 text-right text-xs ${d != null && d < 0 ? "text-removed" : "text-muted"}`}>{d == null ? "" : `${d > 0 ? "+" : ""}${d.toFixed(0)}%`}</td>
                </tr>); })}
            </tbody>
          </table>
        </div>

        <div className="glass-panel p-5">
          <h3 className="mb-3 font-semibold">Meaning preserved?</h3>
          {busy && !run && <div className="flex flex-col gap-3"><Skeleton className="w-3/4" /><Skeleton className="w-1/2" /></div>}
          {run && (
            <>
            <dl className="flex flex-col gap-3 text-sm">
              <div>
                <dt className="text-muted">Baseline classifier agreement <span className="text-xs">(Amazon Comprehend, before vs after)</span></dt>
                <dd className="tnum text-2xl font-semibold">{run.report.sentiment ? `${(run.report.sentiment.agreement * 100).toFixed(1)}%` : <span className="text-base font-normal text-muted">not measured</span>}</dd>
                {run.report.sentiment && <dd className="text-xs text-muted">Share of posts whose sentiment label did not change. High is good: the cleaning kept the meaning.</dd>}
              </div>
              <div>
                <dt className="text-muted">Embedding drift <span className="text-xs">(Titan Embed v2, cosine distance)</span></dt>
                <dd className="tnum text-2xl font-semibold">{run.report.embedding_drift != null ? run.report.embedding_drift.toFixed(3) : <span className="text-base font-normal text-muted">not measured</span>}</dd>
                {run.report.embedding_drift != null && <dd className="text-xs text-muted">0 = identical to an embedding model; above ~0.3 means materially different text.</dd>}
              </div>
            </dl>
            {disabledNote.length > 0 && (
              <p className="mt-3 text-xs text-muted">{disabledNote.join(" ")} Enable in .env to measure.</p>
            )}
            </>
          )}
        </div>
      </div>

      {run && (
        <div className="glass-panel p-5">
          <h3 className="mb-3 font-semibold">Step by step</h3>
          <StepWaterfall steps={run.report.steps} />
          <p className="tnum mt-3 text-xs text-muted">{run.report.steps.length} steps in {run.report.steps.reduce((s, x) => s + x.duration_ms, 0).toFixed(0)} ms.</p>
        </div>
      )}

      {run && (
        <div className="glass-panel p-5">
          <div className="flex items-center justify-between gap-3">
            <h3 className="font-semibold">In plain English</h3>
            {run.report.explanation && <button type="button" className="btn-link" onClick={copy}>{copied ? "Copied" : "Copy"}</button>}
          </div>
          {run.report.explanation ? (
            <><p className="mt-2 max-w-[72ch] leading-relaxed">{run.report.explanation}</p><p className="mt-2 text-xs text-muted">Generated from the numbers above; review before quoting.</p></>
          ) : <p className="mt-2 text-sm text-muted">No explanation for this run. {run.report.warnings.filter((w) => w.includes("explanation")).join(" ")}</p>}
        </div>
      )}

      <div className="glass-panel p-5">
        {records.data ? (
          <RecordsGrid pairs={records.data} theme={theme} hasRun={!!run} onSelect={setSelected} />
        ) : (
          <SkeletonLines lines={6} />
        )}
        <Notice error={records.error} className="mt-3" />
      </div>

      {run && (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-rule pt-4">
          <p className="text-sm text-muted">Happy with the result? Next, give every post a label.</p>
          <button type="button" className="btn-primary" onClick={onContinue}>Continue to Label →</button>
        </div>
      )}

      <Suspense fallback={null}>{selected && <DiffDialog pair={selected} onClose={() => setSelected(null)} />}</Suspense>
    </section>
  );
}
