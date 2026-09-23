import type { StepInfo } from "../../api/types";
import { canMove, type PipelineConfig } from "../../hooks/usePipelineConfig";
import { Skeleton } from "../ui/Skeleton";

/** Mirrors MAX_FILL_VALUE_CHARS in preprocessing/missing_data.py. */
const MAX_FILL_VALUE = 40;

interface Props {
  steps: StepInfo[];
  config: PipelineConfig;
  busy: boolean;
  onToggle: (name: string) => void;
  onMove: (name: string, direction: -1 | 1) => void;
  onOptions: (options: Partial<PipelineConfig["options"]>) => void;
  onRun: () => void;
  onBack: () => void;
}

const GROUPS: { id: StepInfo["group"]; title: string; blurb: string }[] = [
  { id: "clean", title: "Clean the text", blurb: "Remove what carries no sentiment." },
  { id: "normalise", title: "Normalise for NLP", blurb: "Turn text into tokens and reduce variants to one form." },
  { id: "final", title: "Handle empty results", blurb: "Finally, drop or fill records emptied by earlier steps." },
];

/** Stage 2. Pick steps, see their trade-offs, then run. */
export function CleanStep({ steps, config, busy, onToggle, onMove, onOptions, onRun, onBack }: Props) {
  const byName = new Map(steps.map((s) => [s.name, s]));
  const active = config.order.filter((n) => config.enabled[n]);
  // A blank placeholder would leave the record empty and defeat the step, so the run is blocked
  // rather than letting the server reject it after the person has waited.
  const fillValid =
    !config.enabled.missing_data ||
    config.options.missing_data_strategy !== "fill" ||
    config.options.missing_data_fill_value.trim().length > 0;

  return (
    <section className="mx-auto flex max-w-5xl flex-col gap-6">
      <div>
        <h2 className="text-2xl font-semibold tracking-tight">Clean and normalise</h2>
        <p className="mt-1 text-muted">Each step is a trade-off: it removes noise a model would otherwise learn from, and sometimes removes signal too. Select only the steps relevant to your data, then continue to Analyze to measure the effect.</p>
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <div className="flex flex-col gap-6">
          {steps.length === 0 && <div className="glass-panel p-5"><Skeleton className="w-40" /><div className="mt-4 flex flex-col gap-3">{Array.from({ length: 3 }, (_, i) => <Skeleton key={i} />)}</div></div>}
          {steps.length > 0 && GROUPS.map((g) => {
            const ordered = config.order.filter((n) => config.groups[n] === g.id);
            return (
              <div key={g.title} className="glass-panel overflow-hidden">
                <div className="border-b border-rule px-5 py-4">
                  <h3 className="font-semibold">{g.title}</h3>
                  <p className="text-sm text-muted">{g.blurb}</p>
                </div>
                <ol className="divide-y divide-rule">
                  {ordered.map((name) => {
                    const info = byName.get(name); if (!info) return null;
                    const on = config.enabled[name];
                    return (
                      <li key={name} className="grid grid-cols-[auto_1fr_auto] gap-x-3 gap-y-2 px-5 py-4">
                        <label className="contents cursor-pointer" aria-label={info.title}>
                          <input type="checkbox" className="mt-1 size-4 accent-accent" checked={on} onChange={() => onToggle(name)} />
                          <span className="min-w-0">
                            <span className={`block font-medium ${on ? "" : "line-through"}`}>{info.title}</span>
                            <span className="block text-sm text-muted">{info.summary}</span>
                          </span>
                        </label>
                        <span className="flex flex-col gap-0.5 text-muted">
                          <button type="button" aria-label={`Move ${info.title} up`} className="px-1 leading-none hover:text-accent disabled:opacity-30" disabled={!canMove(config, name, -1)} onClick={() => onMove(name, -1)}>↑</button>
                          <button type="button" aria-label={`Move ${info.title} down`} className="px-1 leading-none hover:text-accent disabled:opacity-30" disabled={!canMove(config, name, 1)} onClick={() => onMove(name, 1)}>↓</button>
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
                          <div className="col-start-2 col-span-2 flex flex-wrap items-center gap-2 text-sm">
                            <label className="flex items-center gap-2">
                              Empty text:
                              <select
                                className="field w-auto py-1"
                                value={config.options.missing_data_strategy}
                                onChange={(e) => onOptions({ missing_data_strategy: e.target.value as "drop" | "fill" })}
                              >
                                <option value="drop">drop the row</option>
                                <option value="fill">fill with a placeholder</option>
                              </select>
                            </label>
                            {config.options.missing_data_strategy === "fill" && (
                              <>
                                <label className="flex items-center gap-2">
                                  Placeholder:
                                  <input
                                    className="field tnum w-40 py-1"
                                    value={config.options.missing_data_fill_value}
                                    maxLength={MAX_FILL_VALUE}
                                    aria-invalid={!fillValid}
                                    aria-describedby="fill-help"
                                    onChange={(e) => onOptions({ missing_data_fill_value: e.target.value })}
                                  />
                                </label>
                                <p id="fill-help" className={`w-full text-xs ${fillValid ? "text-muted" : "text-warn-ink"}`}>
                                  {fillValid
                                    ? "Becomes a token in the vocabulary, so pick something the posts cannot contain."
                                    : `Enter a placeholder of 1–${MAX_FILL_VALUE} characters.`}
                                </p>
                              </>
                            )}
                          </div>
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
            {active.length === 0 ? <p className="text-sm text-muted">No steps selected. Continue to analyze the original text without cleaning.</p> : (
              <ol className="tnum flex flex-col gap-1 text-sm">
                {active.map((n, i) => <li key={n}><span className="text-muted">{i + 1}.</span> {byName.get(n)?.title ?? n}</li>)}
              </ol>
            )}
            <button type="button" className="btn-primary" disabled={busy || !fillValid} onClick={onRun}>
              {busy ? "Running…" : "Continue to Analyze →"}
            </button>
            <button type="button" className="btn-link self-start" onClick={onBack}>← Back to Collect</button>
          </div>
          <p className="px-1 text-xs text-muted">Continuing runs your selected steps and opens Analyze. Each run starts from the original posts, so you can try combinations freely.</p>
        </aside>
      </div>

      <div className="sticky bottom-0 -mx-4 flex items-center justify-between gap-3 border-t border-rule bg-surface px-4 py-3 lg:hidden">
        <span className="tnum text-sm text-muted">{active.length} of {steps.length} steps on</span>
        <button type="button" className="btn-primary" disabled={busy || !fillValid} onClick={onRun}>{busy ? "Running…" : "Continue to Analyze →"}</button>
      </div>
    </section>
  );
}
