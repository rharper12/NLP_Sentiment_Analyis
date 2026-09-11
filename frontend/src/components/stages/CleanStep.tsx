import type { StepInfo } from "../../api/types";
import type { PipelineConfig } from "../../hooks/usePipelineConfig";
import { Skeleton } from "../ui/Skeleton";

interface Props {
  steps: StepInfo[];
  config: PipelineConfig;
  busy: boolean;
  onToggle: (name: string) => void;
  onMove: (name: string, direction: -1 | 1) => void;
  onOptions: (options: Partial<PipelineConfig["options"]>) => void;
  onExplain: (value: boolean) => void;
  onRun: () => void;
  onBack: () => void;
}

/* Two families of steps. Cleaning fixes the raw text; normalisation reshapes it into tokens a
   model can count. Cleaning always runs first, which is why the groups are fixed in this order. */
const GROUPS: { title: string; blurb: string; steps: string[] }[] = [
  { title: "Clean the text", blurb: "Remove what carries no sentiment and fix broken rows.", steps: ["missing_data", "lowercase", "punctuation"] },
  { title: "Normalise for NLP", blurb: "Turn text into tokens and reduce variants to one form.", steps: ["tokenize", "stopwords", "lemmatize"] },
];

/** Stage 2. Pick steps, see their trade-offs, then run. */
export function CleanStep({ steps, config, busy, onToggle, onMove, onOptions, onExplain, onRun, onBack }: Props) {
  const byName = new Map(steps.map((s) => [s.name, s]));
  const active = config.order.filter((n) => config.enabled[n]);

  return (
    <section className="mx-auto flex max-w-5xl flex-col gap-6">
      <div>
        <h2 className="text-2xl font-semibold tracking-tight">Clean and normalise</h2>
        <p className="mt-1 text-muted">Each step is a trade-off: it removes noise a model would otherwise learn from, and sometimes removes signal too. Turn steps on or off, reorder within a group, then run to measure the effect.</p>
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <div className="flex flex-col gap-6">
          {steps.length === 0 && <div className="glass-panel p-5"><Skeleton className="w-40" /><div className="mt-4 flex flex-col gap-3">{Array.from({ length: 3 }, (_, i) => <Skeleton key={i} />)}</div></div>}
          {steps.length > 0 && GROUPS.map((g) => {
            const ordered = config.order.filter((n) => g.steps.includes(n));
            return (
              <div key={g.title} className="glass-panel overflow-hidden">
                <div className="border-b border-rule px-5 py-4">
                  <h3 className="font-semibold">{g.title}</h3>
                  <p className="text-sm text-muted">{g.blurb}</p>
                </div>
                <ol className="divide-y divide-rule">
                  {ordered.map((name, i) => {
                    const info = byName.get(name); if (!info) return null;
                    const on = config.enabled[name];
                    return (
                      <li key={name} className={`grid grid-cols-[auto_1fr_auto] gap-x-3 gap-y-2 px-5 py-4 ${on ? "" : "opacity-60"}`}>
                        <label className="contents cursor-pointer">
                          <input type="checkbox" className="mt-1 size-4 accent-accent" checked={on} onChange={() => onToggle(name)} />
                          <span className="min-w-0">
                            <span className={`block font-medium ${on ? "" : "line-through"}`}>{info.title}</span>
                            <span className="block text-sm text-muted">{info.summary}</span>
                          </span>
                        </label>
                        <span className="flex flex-col gap-0.5 text-muted">
                          <button type="button" aria-label={`Move ${info.title} up`} className="px-1 leading-none hover:text-accent disabled:opacity-30" disabled={i === 0} onClick={() => onMove(name, -1)}>↑</button>
                          <button type="button" aria-label={`Move ${info.title} down`} className="px-1 leading-none hover:text-accent disabled:opacity-30" disabled={i === ordered.length - 1} onClick={() => onMove(name, 1)}>↓</button>
                        </span>
                        {on && (
                          <details className="col-start-2 col-span-2 text-sm">
                            <summary className="cursor-pointer text-muted hover:text-ink">Why, and what it costs</summary>
                            <div className="mt-2 grid gap-3 rounded-md bg-surface-2 p-3 sm:grid-cols-2">
                              <p><span className="font-medium text-introduced">Helps:</span> {info.strengths}</p>
                              <p><span className="font-medium text-removed">Costs:</span> {info.limitations}</p>
                            </div>
                          </details>
                        )}
                        {on && name === "stopwords" && (
                          <label className="col-start-2 col-span-2 flex items-center gap-2 text-sm">
                            <input type="checkbox" className="accent-accent" checked={config.options.keep_negations} onChange={(e) => onOptions({ keep_negations: e.target.checked })} />
                            Keep negations (not, never, n't). Turn off to see what removing them does.
                          </label>
                        )}
                        {on && name === "missing_data" && (
                          <label className="col-start-2 col-span-2 flex items-center gap-2 text-sm">
                            Empty text:
                            <select className="field w-auto py-1" value={config.options.missing_data_strategy} onChange={(e) => onOptions({ missing_data_strategy: e.target.value as "drop" | "fill" })}>
                              <option value="drop">drop the row</option>
                              <option value="fill">fill with [EMPTY]</option>
                            </select>
                          </label>
                        )}
                      </li>
                    );
                  })}
                </ol>
              </div>
            );
          })}
        </div>

        <aside className="flex flex-col gap-4 lg:sticky lg:top-4 lg:self-start">
          <div className="glass-panel flex flex-col gap-3 p-5">
            <h3 className="font-semibold">Your pipeline</h3>
            {active.length === 0 ? <p className="text-sm text-muted">No steps selected.</p> : (
              <ol className="tnum flex flex-col gap-1 text-sm">
                {active.map((n, i) => <li key={n}><span className="text-muted">{i + 1}.</span> {byName.get(n)?.title ?? n}</li>)}
              </ol>
            )}
            <label className="flex items-start gap-2 border-t border-rule pt-3 text-sm">
              <input type="checkbox" className="mt-1 accent-accent" checked={config.explain} onChange={(e) => onExplain(e.target.checked)} />
              <span>Ask the model to explain the results in plain English <span className="text-muted">(uses Bedrock)</span></span>
            </label>
            <button type="button" className="btn-primary" disabled={busy || active.length === 0} onClick={onRun}>
              {busy ? "Running…" : "Run pipeline and measure →"}
            </button>
            <button type="button" className="btn-link self-start" onClick={onBack}>← Back to Collect</button>
          </div>
          <p className="px-1 text-xs text-muted">Nothing is changed until you run. Each run starts from the original posts, so you can try combinations freely.</p>
        </aside>
      </div>

      <div className="sticky bottom-0 -mx-4 flex items-center justify-between gap-3 border-t border-rule bg-surface/95 px-4 py-3 backdrop-blur lg:hidden">
        <span className="tnum text-sm text-muted">{active.length} of {steps.length} steps on</span>
        <button type="button" className="btn-primary" disabled={busy || active.length === 0} onClick={onRun}>{busy ? "Running…" : "Run and measure →"}</button>
      </div>
    </section>
  );
}
