import { useEffect, useState } from "react";

import { api } from "../../api/client";
import type { CheckpointInfo, CheckpointList, CheckpointStage, DatasetSummary, PreprocessResponse } from "../../api/types";
import { useAsync } from "../../hooks/useAsync";
import { Notice } from "../ui/Notice";
import { SkeletonLines } from "../ui/Skeleton";

interface Props { dataset: DatasetSummary; run: PreprocessResponse | null; diagnostics: boolean; onBack: () => void; onStartOver: () => void }

const kb = (n: number) => (n > 1_048_576 ? `${(n / 1_048_576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);
const STAGE_BLURB: { [k in CheckpointStage]: string } = { collected: "raw posts as fetched", processed: "after the pipeline", labelled: "with labels and provenance" };

/** Stage 5. Downloads, the S3 save, and the checkpoint snapshots with one-click Parquet conversion. */
export function ExportStep({ dataset, run, diagnostics, onBack, onStartOver }: Props) {
  const save = useAsync<{ uri: string }>();
  const checkpoints = useAsync<CheckpointList>();
  const { run: loadCheckpoints } = checkpoints;
  const [uri, setUri] = useState<string | null>(null);
  const [converting, setConverting] = useState<CheckpointStage | null>(null);
  const id = dataset.dataset_id;

  useEffect(() => {
    if (diagnostics) void loadCheckpoints((signal) => api.checkpoints(id, signal));
  }, [id, diagnostics, loadCheckpoints]);

  const convert = async (stage: CheckpointStage) => {
    setConverting(stage);
    try { await api.convertCheckpoint(id, stage); await checkpoints.run((s) => api.checkpoints(id, s)); } finally { setConverting(null); }
  };

  const downloads = [
    { title: "Parquet", body: "Typed, compressed, one line to load in pandas. Use this for Task 2.", kind: "parquet" as const, name: `${id}.parquet` },
    { title: "CSV", body: "Original and processed text, labels and provenance, one row per post. Opens anywhere.", kind: "csv" as const, name: `${id}.csv` },
    { title: "Excel", body: "Same data plus an impact sheet with the per-step statistics.", kind: "xlsx" as const, name: `${id}.xlsx` },
    { title: "Report (Markdown)", body: "Provenance, steps applied, measured impact, labelling summary, and the strengths and limitations of each technique. Paste into your write-up.", kind: "md" as const, name: `${id}-report.md`, needsRun: true },
  ];
  const byStage = new Map<string, CheckpointInfo[]>();
  for (const c of checkpoints.data?.items ?? []) byStage.set(c.stage, [...(byStage.get(c.stage) ?? []), c]);

  return (
    <section className="mx-auto flex max-w-3xl flex-col gap-6">
      <div>
        <h2 className="text-2xl font-semibold tracking-tight">Export</h2>
        <p className="mt-1 text-muted"><span className="tnum">{dataset.record_count.toLocaleString()}</span> posts{run ? `, ${run.applied_steps.length} steps applied` : ", not yet processed"}. Columns: id, source, label, label_source, label_confidence, comprehend_label, comprehend_confidence, created_at, original_text, processed_text, tokens.</p>
      </div>

      <ul className="glass-panel divide-y divide-rule">
        {downloads.map((it) => (
          <li key={it.title} className="flex flex-col gap-3 p-5 sm:flex-row sm:items-center sm:justify-between">
            <div><h3 className="font-semibold">{it.title}</h3><p className="text-sm text-muted">{it.body}</p></div>
            <a className="btn shrink-0" href={api.exportUrl(id, it.kind)} download={it.name} aria-disabled={it.needsRun && !run}>Download</a>
          </li>
        ))}
        <li className="flex flex-col gap-3 p-5 sm:flex-row sm:items-center sm:justify-between">
          <div><h3 className="font-semibold">Save to S3</h3><p className="text-sm text-muted">Parquet + impact.json + manifest.json in your bucket, timestamped.</p>{uri && <p className="tnum mt-1 break-all text-xs text-muted">Saved to {uri}</p>}</div>
          <button type="button" className="btn shrink-0" disabled={save.loading} onClick={async () => { const r = await save.run((s) => api.save(id, s)); if (r) setUri(r.uri); }}>{save.loading ? "Saving…" : "Save"}</button>
        </li>
      </ul>
      <Notice error={save.error ?? checkpoints.error} />

      {diagnostics && <div className="glass-panel p-5">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h3 className="font-semibold">Checkpoints</h3>
          <span className="text-xs text-muted">Written automatically to {checkpoints.data?.location === "s3" ? "S3" : "data/checkpoints (gitignored)"} as each stage completes.</span>
        </div>
        {checkpoints.loading && !checkpoints.data && <div className="mt-3"><SkeletonLines lines={3} /></div>}
        {checkpoints.data && (
          <ul className="mt-3 divide-y divide-rule text-sm">
            {(["collected", "processed", "labelled"] as CheckpointStage[]).map((stage) => {
              const files = byStage.get(stage) ?? [];
              const hasCsv = files.some((f) => f.format === "csv"), hasParquet = files.some((f) => f.format === "parquet");
              return (
                <li key={stage} className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:justify-between">
                  <div>
                    <span className="font-medium capitalize">{stage}</span> <span className="text-muted">· {STAGE_BLURB[stage]}</span>
                    {files.length === 0 ? <p className="text-xs text-muted">not yet written</p> : files.map((f) => <p key={f.format} className="tnum break-all text-xs text-muted">{f.format} · {kb(f.bytes)} · {f.uri}</p>)}
                  </div>
                  {hasCsv && <button type="button" className="btn shrink-0" disabled={converting !== null} onClick={() => void convert(stage)}>{converting === stage ? "Converting…" : hasParquet ? "Re-convert to Parquet" : "Convert to Parquet"}</button>}
                </li>
              );
            })}
          </ul>
        )}
      </div>}

      <div className="flex flex-wrap justify-between gap-3">
        <button type="button" className="btn" onClick={onBack}>← Back to Label</button>
        <button type="button" className="btn" onClick={onStartOver}>Start a new topic</button>
      </div>
    </section>
  );
}
