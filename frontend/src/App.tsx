import { Suspense, lazy, useEffect, useRef, useState, type ReactNode } from "react";

import { api } from "./api/client";
import type {
  CollectionWindow,
  DatasetSummary,
  HealthResponse,
  PreprocessResponse,
  StepInfo,
} from "./api/types";
import { DocumentTitle } from "./components/DocumentTitle";
import { Header } from "./components/Header";
import { stagesFor, Stepper, type Stage } from "./components/Stepper";
import { CleanStep } from "./components/stages/CleanStep";
import { CollectStep } from "./components/stages/CollectStep";
import { ExportStep } from "./components/stages/ExportStep";
import { LabelStep } from "./components/stages/LabelStep";
import { ErrorBoundary } from "./components/ui/ErrorBoundary";
import { SkeletonLines } from "./components/ui/Skeleton";
import { useAsync } from "./hooks/useAsync";
import { usePipelineConfig } from "./hooks/usePipelineConfig";
import { useTheme } from "./hooks/useTheme";

const HistoryDialog = lazy(() => import("./components/HistoryDialog"));
const AnalyzeStep = lazy(() =>
  import("./components/stages/AnalyzeStep").then((module) => ({
    default: module.AnalyzeStep,
  })),
);

/**
 * Own dataset and pipeline state across five stages. Consumer collections can go directly
 * to review; other collections reach Label after preprocessing produces a result.
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

  const [xRequest, setXRequest] = useState<{
    id: string;
    query: string;
    limit: number;
    window: CollectionWindow;
  } | null>(null);
  const [labelled, setLabelled] = useState(false);
  const [reviewActive, setReviewActive] = useState(false);
  const [collecting, setCollecting] = useState(false);
  const [consumerSelected, setConsumerSelected] = useState(false);
  const [collectionMessage, setCollectionMessage] = useState("");
  const collectionInFlight = useRef(false);
  const consumer = !!dataset.data?.consumer_policy;
  const consumerFlow = dataset.data ? consumer : consumerSelected;
  const order = (s: Stage) => stagesFor(consumerFlow).findIndex((x) => x.id === s);
  const reached: Stage = dataset.loading
    ? "collect"
    : run.loading
      ? "analyze"
      : consumer
        ? run.data
          ? "export"
          : labelled
            ? "clean"
            : "label"
        : labelled
          ? "export"
          : run.data
            ? "label"
            : dataset.data
              ? "clean"
              : "collect";
  const go = (s: Stage) => {
    if (!reviewActive && order(s) <= order(reached)) setStage(s);
  };

  const collect = async (
    task: (signal: AbortSignal) => Promise<DatasetSummary>,
    openNextStage = false,
    recoveryId?: string,
  ) => {
    if (collectionInFlight.current) return null;
    collectionInFlight.current = true;
    setCollecting(true);
    setCollectionMessage("");
    const previous = dataset.data;
    run.reset();
    setLabelled(false);
    setRunVersion((v) => v + 1);
    let result = await dataset.run(task);
    const interrupted = !result;
    // An interrupted response can follow committed, paid pages. Read saved totals before
    // offering another request; recovery never fetches from X.
    if (!result && recoveryId) result = await dataset.run((signal) => api.dataset(recoveryId, signal));
    collectionInFlight.current = false;
    setCollecting(false);
    if (result) {
      const before = previous?.dataset_id === result.dataset_id ? previous.record_count : 0;
      const added = Math.max(0, result.record_count - before);
      setCollectionMessage(interrupted
        ? `Request interrupted. Saved progress recovered: ${result.record_count.toLocaleString()} posts. Check collection status before resuming.`
        : `${added ? `Added ${added.toLocaleString()} posts.` : "No new posts were added."} ${result.record_count.toLocaleString()} posts are saved in this dataset.`);
    }
    if (result?.source_type === "x") {
      setSpendVersion((v) => v + 1);
      if (!result.partial) setXRequest(null);
    }
    if (result && openNextStage) {
      dispatch({ type: "init", steps: steps.data ?? [] });
      dispatch({
        type: "options",
        options: {
          keep_negations: !!result.consumer_policy,
          missing_data_strategy: "drop",
          missing_data_fill_value: "[EMPTY]",
        },
      });
      setStage(result.consumer_policy ? "label" : "clean");
    }
    return result;
  };

  const collectAdditional = async (target: number) => {
    const current = dataset.data;
    if (!current) return;
    await collect((signal) =>
      api.collectCandidates(current.dataset_id, target, signal),
      false, current.dataset_id,
    );
  };

  const runPipeline = async () => {
    const current = dataset.data;
    if (!current) return;
    setStage("analyze");
    const result = await run.run((signal) =>
      api.preprocess(current.dataset_id, activeSteps, config.options, signal),
    );
    if (result) setRunVersion((v) => v + 1);
  };

  const startOver = () => {
    run.reset();
    dataset.reset();
    setXRequest(null);
    setLabelled(false);
    setCollectionMessage("");
    setConsumerSelected(false);
    setStage("collect");
  };

  return (
    <div className="flex min-h-screen flex-col">
      <DocumentTitle
        stage={stage}
        records={dataset.data?.record_count ?? null}
        consumer={consumerFlow}
      />
      <Header
        diagnostics={health.data?.diagnostics ?? false}
        theme={theme}
        onToggleTheme={toggle}
        onOpenHistory={() => setHistoryOpen(true)}
        spendVersion={spendVersion}
      />
      <Stepper
        disabled={reviewActive || (consumer && dataset.loading)}
        current={stage}
        reached={reached}
        onSelect={go}
        consumer={consumerFlow}
      />
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 sm:px-6 sm:py-8">
        <ErrorBoundary>
          <Suspense fallback={<div className="glass-panel p-6"><p role="status" className="mb-5">Loading {stage}…</p><SkeletonLines lines={6} /></div>}>
            <StagePanel stage={stage}>
              {stage === "collect" && (
                <CollectStep
                  busy={dataset.loading}
                  error={dataset.error}
                  dataset={dataset.data}
                  collectionMessage={collectionMessage}
                  onModeChange={setConsumerSelected}
                  costPerRead={health.data?.x_cost_per_read_usd}
                  localDatasetsAvailable={health.data?.local_datasets_available === true}
                  xConfigured={health.data?.x_configured ?? true}
                  onSearch={(q, n, window) => {
                    if (collectionInFlight.current) return;
                    const sameRequest = xRequest?.query === q && xRequest.limit === n
                      && JSON.stringify(xRequest.window) === JSON.stringify(window);
                    const id = sameRequest ? xRequest.id : crypto.randomUUID();
                    if (window.preset === "consumer_reactions")
                      dispatch({
                        type: "options",
                        options: { ...config.options, keep_negations: true },
                      });
                    setXRequest({ id, query: q, limit: n, window });
                    void collect((s) => api.load("x", n, q, window, s, id), false, `x-${id}`);
                  }}
                  onResume={
                    dataset.data?.consumer_policy
                      ? undefined
                      : xRequest
                        ? () =>
                            void collect((s) =>
                              api.load(
                                "x",
                                xRequest.limit,
                                xRequest.query,
                                xRequest.window,
                                s,
                                xRequest.id,
                              ), false, `x-${xRequest.id}`,
                            )
                        : undefined
                  }
                  onAdditional={(target) => void collectAdditional(target)}
                  onLoadSample={(n) => {
                    setXRequest(null);
                    void collect((s) => api.load("huggingface", n, "", undefined, s));
                  }}
                  onUpload={(f) => {
                    setXRequest(null);
                    void collect((s) => api.upload(f, s));
                  }}
                  onRestore={(f) => {
                    setXRequest(null);
                    void collect((s) => api.restoreLocal(f, s), true);
                  }}
                  onCancel={dataset.cancel}
                  onContinue={() => setStage(dataset.data?.consumer_policy ? "label" : "clean")}
                />
              )}
              {stage === "clean" && (
                <CleanStep
                  steps={steps.data ?? []}
                  config={config}
                  busy={run.loading}
                  onToggle={(name) => dispatch({ type: "toggle", name })}
                  onMove={(name, direction) => dispatch({ type: "move", name, direction })}
                  onOptions={(options) => dispatch({ type: "options", options })}
                  onRun={() => void runPipeline()}
                  onBack={() => setStage(consumer ? "label" : "collect")}
                  backLabel={consumer ? "Review & label" : "Collect"}
                />
              )}
              {stage === "analyze" && dataset.data && (
                <AnalyzeStep
                  key={dataset.data.dataset_id}
                  diagnostics={health.data?.diagnostics ?? false}
                  datasetId={dataset.data.dataset_id}
                  recordCount={dataset.data.record_count}
                  theme={theme}
                  run={run.data}
                  busy={run.loading}
                  error={run.error}
                  runVersion={runVersion}
                  onCancel={run.cancel}
                  onRerun={() => void runPipeline()}
                  onBack={() => setStage("clean")}
                  onContinue={() => setStage(consumer ? "export" : "label")}
                  continueLabel={consumer ? "Export" : "Label"}
                />
              )}
              {stage === "label" && dataset.data && (
                <LabelStep
                  onReviewActiveChange={setReviewActive}
                  datasetId={dataset.data.dataset_id}
                  consumerDataset={dataset.data}
                  collectionLoading={collecting}
                  collectionError={dataset.error}
                  collectionMessage={collectionMessage}
                  costPerRead={health.data?.x_cost_per_read_usd}
                  onAdditional={(target) => void collectAdditional(target)}
                  onChanged={(updated) => {
                    run.reset();
                    setRunVersion((v) => v + 1);
                    void dataset.run(async () => updated);
                  }}
                  diagnostics={health.data?.diagnostics ?? false}
                  comprehendEnabled={health.data?.comprehend_enabled ?? null}
                  checkpointLocation={health.data?.checkpoints ?? null}
                  onBack={() => setStage(consumer ? "collect" : "analyze")}
                  onContinue={() => {
                    setLabelled(true);
                    setStage(consumer ? "clean" : "export");
                  }}
                />
              )}
              {stage === "export" && dataset.data && (
                <ExportStep
                  key={dataset.data.dataset_id}
                  dataset={dataset.data}
                  run={run.data}
                  diagnostics={health.data?.diagnostics ?? false}
                  onBack={() => setStage(consumer ? "analyze" : "label")}
                  onStartOver={startOver}
                />
              )}
            </StagePanel>
          </Suspense>
        </ErrorBoundary>
      </main>
      <footer className="border-t border-rule py-4 text-center text-xs text-muted sm:hidden">
        <button type="button" className="btn-link" onClick={() => setHistoryOpen(true)}>
          Run history
        </button>
      </footer>
      <Suspense fallback={null}>
        {historyOpen && (
          <HistoryDialog
            ephemeral={health.data?.database_ephemeral === true}
            onClose={() => setHistoryOpen(false)}
          />
        )}
      </Suspense>
    </div>
  );
}

/** Focus stage changes after Suspense resolves, without moving focus on initial page load. */
function StagePanel({ stage, children }: { stage: Stage; children: ReactNode }) {
  const panel = useRef<HTMLDivElement>(null);
  const previousStage = useRef(stage);
  useEffect(() => {
    if (previousStage.current === stage) return;
    previousStage.current = stage;
    const heading = panel.current?.querySelector("h2");
    if (heading) {
      heading.tabIndex = -1;
      heading.focus();
    }
  }, [stage]);
  return <div ref={panel}>{children}</div>;
}
