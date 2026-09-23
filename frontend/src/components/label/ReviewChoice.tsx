import { useId, useState } from "react";

import type { ReviewMode } from "../../api/types";
import type { SampleUnit } from "./sampleSize";

import { convertOnUnitSwitch, maxFor, normaliseSize, resolveSample } from "./sampleSize";

interface Props {
  total: number;
  unreviewed?: number;
  machineScored?: number;
  onChoose: (mode: ReviewMode, size: number, unit: SampleUnit) => void;
  onBack: () => void;
}

const MODES: [ReviewMode, string, string][] = [
  ["low_confidence", "Lowest confidence first (recommended)", "Find likely errors: unscored posts first, then the lowest Comprehend scores."],
  ["none", "Skip review", "Keep Comprehend's labels as they are."],
  ["sample", "Review a sample", "Random selection for a broader check; agreement is not accuracy."],
  ["all", "Review everything", "Every post gets a human label."],
];

/** Choose the review scope. Sample size is editable as a post count or a percent of the dataset. */
export function ReviewChoice({ total, unreviewed = total, machineScored = 0, onChoose, onBack }: Props) {
  const [mode, setMode] = useState<ReviewMode>(machineScored > 0 && unreviewed > 0 ? "low_confidence" : "sample");
  const [unit, setUnit] = useState<SampleUnit>("count");
  const [raw, setRaw] = useState("150");
  const id = useId();

  const population = mode === "low_confidence" ? unreviewed : total;
  const sized = mode === "sample" || mode === "low_confidence";
  const sample = resolveSample(raw, unit, population);
  const effective = mode === "all" ? total : mode === "none" ? 0 : sample.posts;
  const max = maxFor(unit, population);

  const switchUnit = (next: SampleUnit) => {
    if (next === unit) return;
    setRaw(convertOnUnitSwitch(sample, next));
    setUnit(next);
  };

  return (
    <div className="glass-panel flex flex-col gap-4 p-5">
      <h3 className="font-semibold">How much will you review by hand?</h3>
      <div className="grid gap-2 sm:grid-cols-2">
        {MODES.map(([m, title, blurb]) => (
          <label key={m} data-selected={mode === m} className="selectable flex cursor-pointer flex-col gap-1 p-3">
            <span className="flex items-center gap-2 font-medium">
              <input type="radio" name="review-mode" className="accent-accent" checked={mode === m} onChange={() => setMode(m)} />
              {title}
            </span>
            <span className="text-xs text-muted">{blurb}</span>
          </label>
        ))}
      </div>

      {sized && (
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex flex-col gap-1">
              <label htmlFor={id} className="text-sm text-muted">Sample size</label>
              <input
                id={id}
                type="text"
                inputMode="numeric"
                autoComplete="off"
                aria-label={unit === "count" ? "Sample size in posts" : "Sample size as a percent"}
                aria-describedby={`${id}-help`}
                aria-invalid={!sample.valid}
                value={raw}
                onChange={(e) => setRaw(normaliseSize(e.target.value, unit, population))}
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
            {sample.valid ? (
              <>
                <span className="tnum font-medium text-ink">{sample.posts.toLocaleString()} posts</span>{" "}
                <span className="tnum">({sample.percent}% of {population.toLocaleString()})</span>. {mode === "low_confidence" ? "Already reviewed posts are excluded. This selection helps find errors; it cannot estimate overall accuracy." : "Chosen at random. Larger samples reduce uncertainty; agreement with Comprehend is not an accuracy score."}
              </>
            ) : (
              <span className="text-warn-ink">
                Enter a number between 1 and {max.toLocaleString()}{unit === "percent" ? "%" : " posts"}.
              </span>
            )}
          </p>
        </div>
      )}

      <div className="flex flex-wrap justify-between gap-3 border-t border-rule pt-4">
        <button type="button" className="btn" onClick={onBack}>← Back</button>
        <button
          type="button"
          className="btn-primary"
          disabled={(sized && !sample.valid) || (mode !== "none" && effective === 0)}
          onClick={() => onChoose(mode, sized ? sample.posts : 1, "count")}
        >
          {mode === "none" ? "Skip review" : `Start reviewing ${effective.toLocaleString()} posts`}
        </button>
      </div>
    </div>
  );
}
