import { Suspense, lazy, useCallback, useEffect, useState } from "react";

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
  diagnostics: boolean;
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

/** Stage 3. The numbers first, then a chart, then the evidence (records). */
export function AnalyzeStep({ diagnostics, datasetId, recordCount, theme, run, busy, error, runVersion, onCancel, onRerun, onBack, onContinue }: Props) {
  const [selected, setSelected] = useState<{ pair: RecordPair; trigger?: HTMLElement } | null>(null);
  const selectRecord = useCallback((pair: RecordPair, trigger?: HTMLElement) => setSelected({ pair, trigger }), []);
  const records = useAsync<RecordPair[]>();
  const { run: loadRecords } = records;
  // `warnings` is optional in the schema (it has a server-side default), so normalise once.
  const allWarnings = [...(run?.report.warnings ?? []), ...(run?.warnings ?? [])];
  const warnings = allWarnings.filter((w) => !w.includes("disabled"));
  const disabledNote = allWarnings.filter((w) => w.includes("disabled"));

  const rows = run ? [
    { label: "Posts", a: run.metrics_before.record_count, b: run.metrics_after.record_count, f: fmt },
    { label: "Vocabulary", a: run.metrics_before.vocab_size, b: run.metrics_after.vocab_size, f: fmt, help: "distinct tokens counted in this representation" },
    { label: "Tokens per post", a: run.metrics_before.avg_tokens, b: run.metrics_after.avg_tokens, f: (n: number) => n.toFixed(1) },
    { label: "Type–token ratio", a: run.metrics_before.type_token_ratio, b: run.metrics_after.type_token_ratio, f: (n: number) => n.toFixed(3), help: "distinct tokens ÷ total tokens" },
  ] : [];

  // Reload whenever a pipeline run finishes so the processed column reflects the latest run.
  useEffect(() => {
    void loadRecords((signal) => api.allRecords(datasetId, recordCount, signal));
  }, [datasetId, recordCount, runVersion, loadRecords]);

  return (
    <section className="mx-auto flex max-w-5xl flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight">What changed</h2>
          <p className="mt-1 text-muted">Before → after for the whole dataset, then step by step, then post by post.</p>
        </div>
        <div className="flex gap-2">
          <button type="button" className="btn" onClick={onBack}>← Adjust steps</button>
          {busy ? <button type="button" className="btn" onClick={onCancel}>Cancel</button> : <button type="button" className="btn" onClick={onRerun}>{run?.partial ? "Resume analysis" : "Run again"}</button>}
        </div>
      </div>

      <Notice error={error} warnings={warnings} />

      <div className="grid gap-6 lg:grid-cols-2">
        <div className="glass-panel p-5">
          <h3 className="mb-3 font-semibold">Dataset</h3>
          <table className="tnum w-full text-sm">
            <thead><tr><th scope="col" className="text-left">Metric</th><th scope="col" className="pl-3 text-right">Original</th><th scope="col" className="pl-3 text-right">Processed</th><th scope="col" className="pl-3 text-right">Change</th></tr></thead>
            <tbody>
              {busy && rows.length === 0 && Array.from({ length: 4 }, (_, i) => <tr key={i}><td className="py-2 text-muted"><Skeleton className="w-24" /></td><td className="py-2 text-right"><Skeleton className="w-12" /></td><td className="py-2 text-right"><Skeleton className="w-14" /></td><td className="py-2 pl-3 text-right"><Skeleton className="w-10" /></td></tr>)}
              {rows.map((r) => { const d = pct(r.a, r.b); return (
                <tr key={r.label} className="border-t border-rule first:border-0">
                  <th scope="row" className="py-2 pr-2 text-left font-medium text-muted">{r.label}{r.help && <span className="block max-w-[24ch] text-xs font-normal">{r.help}</span>}</th>
                  <td className="py-2 pl-3 text-right">{r.f(r.a)}</td>
                  <td className="py-2 pl-3 text-right text-lg font-semibold">{r.f(r.b)}</td>
                  <td className={`py-2 pl-3 text-right text-xs ${d != null && d < 0 ? "text-removed" : "text-muted"}`}>{d == null ? "" : `${d > 0 ? "+" : ""}${d.toFixed(1)}%`}</td>
                </tr>); })}
            </tbody>
          </table>
          <p className="mt-3 text-xs text-muted">Token counts depend on how the text is split. Selecting tokenization can increase vocabulary and token counts even when words are unchanged. These metrics do not measure sentiment accuracy.</p>
        </div>

        <div className="glass-panel p-5">
          <h3 className="mb-3 font-semibold">Prediction consistency</h3>
          {busy && !run && <div className="flex flex-col gap-3"><Skeleton className="w-3/4" /><Skeleton className="w-1/2" /></div>}
          {run && (
            <>
            <dl className="flex flex-col gap-3 text-sm">
              <div>
                <dt className="text-muted">Same prediction before and after <span className="text-xs">(Amazon Comprehend, before vs after)</span></dt>
                <dd className="tnum text-2xl font-semibold">{run.report.sentiment?.agreement != null ? `${(run.report.sentiment.agreement * 100).toFixed(1)}%` : <span className="text-base font-normal text-muted">not measured</span>}</dd>
                {run.report.sentiment && <dd className="text-xs text-muted">{run.report.sentiment.comparable_records ?? 0} successfully scored comparable posts of {run.report.sentiment.shared_records ?? 0} shared posts. Agreement is unavailable when none were scored successfully.</dd>}
              </div>
            </dl>
            <p className="mt-3 text-sm text-muted">Agreement measures consistency, not accuracy or proof that meaning was preserved. Comprehend can make the same mistake on both versions. It predicts overall post sentiment, which may differ from sentiment toward your subject.</p>
            {run.report.sentiment && <>
              {run.report.sentiment.agreement != null && <p className="mt-3 text-sm">{(run.report.sentiment.comparable_records ?? 0) - Math.round(run.report.sentiment.agreement * (run.report.sentiment.comparable_records ?? 0))} comparable posts changed prediction.</p>}
              <table className="tnum mt-3 w-full text-sm">
                <caption className="mb-2 text-left text-muted">Comprehend predictions on successfully scored posts</caption>
                <thead><tr><th scope="col" className="text-left">Sentiment</th><th scope="col" className="pl-3 text-right">Original</th><th scope="col" className="pl-3 text-right">Processed</th></tr></thead>
                <tbody>{["positive", "negative", "neutral", "mixed"].map((label) => <tr key={label}><th scope="row" className="text-left font-normal capitalize">{label}</th><td className="text-right">{run.report.sentiment?.distribution_before[label] ?? 0}</td><td className="text-right">{run.report.sentiment?.distribution_after[label] ?? 0}</td></tr>)}</tbody>
              </table>
            </>}
            {diagnostics && disabledNote.length > 0 && (
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
          {diagnostics && <p className="tnum mt-3 text-xs text-muted">{run.report.steps.length} steps in {run.report.steps.reduce((s, x) => s + (x.duration_ms ?? 0), 0).toFixed(0)} ms.</p>}
        </div>
      )}

      <div className="glass-panel p-5">
        {records.data ? (
          <RecordsGrid pairs={records.data} theme={theme} hasRun={!!run} onSelect={selectRecord} />
        ) : (
          <SkeletonLines lines={6} />
        )}
        <Notice error={records.error} className="mt-3" />
      </div>

      {run && (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-rule pt-4">
          <p className="text-sm text-muted">Inspect changed posts before assigning or reviewing sentiment labels.</p>
          <button type="button" className="btn-primary" disabled={busy} onClick={onContinue}>Continue to Label →</button>
        </div>
      )}

      <Suspense fallback={null}>{selected && <DiffDialog pair={selected.pair} trigger={selected.trigger} onClose={() => setSelected(null)} />}</Suspense>
    </section>
  );
}
