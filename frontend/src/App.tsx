import { Suspense, lazy, useEffect, useState } from "react";

import { api } from "./api/client";
import type { DatasetSummary, HealthResponse, PreprocessResponse, StepInfo } from "./api/types";
import { Header } from "./components/Header";
import { STAGES, Stepper, type Stage } from "./components/Stepper";
import { AnalyzeStep } from "./components/stages/AnalyzeStep";
import { CleanStep } from "./components/stages/CleanStep";
import { CollectStep } from "./components/stages/CollectStep";
import { ExportStep } from "./components/stages/ExportStep";
import { LabelStep } from "./components/stages/LabelStep";
import { ErrorBoundary } from "./components/ui/ErrorBoundary";
import { useAsync } from "./hooks/useAsync";
import { usePipelineConfig } from "./hooks/usePipelineConfig";
import { useTheme } from "./hooks/useTheme";

const HistoryDialog = lazy(() => import("./components/HistoryDialog"));

const order = (s: Stage) => STAGES.findIndex((x) => x.id === s);

/**
 * A guided four-stage flow. State lives here; each stage is a presentational component that
 * receives what it needs. A stage is reachable once the previous one has produced its result.
 */
export default function App() {
  const { theme, toggle } = useTheme();
  const health = useAsync<HealthResponse>();
  const steps = useAsync<StepInfo[]>();
  const dataset = useAsync<DatasetSummary>();
  const run = useAsync<PreprocessResponse>();
  const { config, dispatch, activeSteps } = usePipelineConfig();
  const { run: loadHealth } = health;
  const { run: loadSteps } = steps;
  const [stage, setStage] = useState<Stage>("collect");
  const [runVersion, setRunVersion] = useState(0);
  const [spendVersion, setSpendVersion] = useState(0);
  const [historyOpen, setHistoryOpen] = useState(false);

  useEffect(() => {
    void loadHealth(api.health);
    void loadSteps(api.steps).then((list) => list && dispatch({ type: "init", steps: list }));
  }, [loadHealth, loadSteps, dispatch]);

  const [labelled, setLabelled] = useState(false);
  const reached: Stage = labelled ? "export" : run.data ? "label" : dataset.data ? "clean" : "collect";
  const go = (s: Stage) => { if (order(s) <= order(reached)) setStage(s); };

  const collect = async (task: (signal: AbortSignal) => Promise<DatasetSummary>) => {
    run.reset();
    setLabelled(false);
    setRunVersion((v) => v + 1);
    const result = await dataset.run(task);
    if (result?.source_type === "x") setSpendVersion((v) => v + 1);
  };

  const runPipeline = async () => {
    const current = dataset.data;
    if (!current) return;
    setStage("analyze");
    const result = await run.run((signal) =>
      api.preprocess(current.dataset_id, activeSteps, config.options, config.explain, signal),
    );
    if (result) setRunVersion((v) => v + 1);
  };

  const startOver = () => { run.reset(); dataset.reset(); setLabelled(false); setStage("collect"); };

  return (
    <div className="flex min-h-screen flex-col">
      <Header theme={theme} onToggleTheme={toggle} onOpenHistory={() => setHistoryOpen(true)} spendVersion={spendVersion} />
      <Stepper current={stage} reached={reached} onSelect={go} />
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 sm:px-6 sm:py-8">
        <ErrorBoundary>
          {stage === "collect" && (
            <CollectStep
              busy={dataset.loading} error={dataset.error} dataset={dataset.data}
              xConfigured={health.data?.x_configured ?? true}
              onSearch={(q, n) => void collect((s) => api.load("x", n, q, s))}
              onLoadSample={(n) => void collect((s) => api.load("huggingface", n, "", s))}
              onUpload={(f) => void collect((s) => api.upload(f, s))}
              onCancel={dataset.cancel}
              onContinue={() => setStage("clean")}
            />
          )}
          {stage === "clean" && (
            <CleanStep
              steps={steps.data ?? []} config={config} busy={run.loading}
              onToggle={(name) => dispatch({ type: "toggle", name })}
              onMove={(name, direction) => dispatch({ type: "move", name, direction })}
              onOptions={(options) => dispatch({ type: "options", options })}
              onExplain={(value) => dispatch({ type: "explain", value })}
              onRun={() => void runPipeline()}
              onBack={() => setStage("collect")}
            />
          )}
          {stage === "analyze" && dataset.data && (
            <AnalyzeStep
              datasetId={dataset.data.dataset_id} recordCount={dataset.data.record_count} theme={theme}
              run={run.data} busy={run.loading} error={run.error} runVersion={runVersion}
              onCancel={run.cancel} onRerun={() => void runPipeline()}
              onBack={() => setStage("clean")} onContinue={() => setStage("label")}
            />
          )}
          {stage === "label" && dataset.data && (
            <LabelStep
              datasetId={dataset.data.dataset_id}
              diagnostics={health.data?.diagnostics ?? false}
              comprehendEnabled={health.data?.comprehend_enabled ?? null}
              checkpointLocation={health.data?.checkpoints ?? null}
              onBack={() => setStage("analyze")}
              onContinue={() => { setLabelled(true); setStage("export"); }}
            />
          )}
          {stage === "export" && dataset.data && (
            <ExportStep dataset={dataset.data} run={run.data} diagnostics={health.data?.diagnostics ?? false} onBack={() => setStage("label")} onStartOver={startOver} />
          )}
        </ErrorBoundary>
      </main>
      <footer className="border-t border-rule py-4 text-center text-xs text-muted sm:hidden">
        <button type="button" className="btn-link" onClick={() => setHistoryOpen(true)}>Run history</button>
      </footer>
      <Suspense fallback={null}>
        {historyOpen && <HistoryDialog ephemeral={health.data?.database_ephemeral === true} onClose={() => setHistoryOpen(false)} />}
      </Suspense>
    </div>
  );
}
