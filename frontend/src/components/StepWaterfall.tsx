import type { StepResult } from "../api/types";

const W = 640, H = 160, PAD = { left: 8, right: 8, top: 22, bottom: 30 };

/** Vocabulary after each step, left to right in the order run. The one chart in the app. */
export function StepWaterfall({ steps }: { steps: StepResult[] }) {
  if (steps.length === 0) return null;
  const values = [steps[0].vocab_before, ...steps.map((s) => s.vocab_after)];
  const labels = ["start", ...steps.map((s) => s.step_name.replace("_", " "))];
  const max = Math.max(...values, 1);
  const innerW = W - PAD.left - PAD.right, innerH = H - PAD.top - PAD.bottom;
  const slot = innerW / values.length, barW = Math.min(60, slot * 0.6);
  return (
    <figure className="m-0 w-full max-w-2xl">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Vocabulary size after each step" className="block h-auto w-full">
        {values.map((v, i) => {
          const h = (v / max) * innerH, x = PAD.left + slot * i + (slot - barW) / 2, y = PAD.top + innerH - h;
          const drop = i === 0 ? 0 : values[i - 1] - v;
          return (
            <g key={labels[i] + i}>
              <rect x={x} y={y} width={barW} height={h} rx={3} className={i === 0 ? "fill-muted/50" : "fill-accent"} />
              <text x={x + barW / 2} y={y - 6} textAnchor="middle" className="tnum fill-ink text-[11px]">{v.toLocaleString()}</text>
              <text x={x + barW / 2} y={PAD.top + innerH + 13} textAnchor="middle" className="fill-muted text-[11px]">{labels[i]}</text>
              {drop !== 0 && <text x={x + barW / 2} y={PAD.top + innerH + 26} textAnchor="middle" className="tnum fill-removed text-[10.5px]">{drop > 0 ? `−${drop.toLocaleString()}` : `+${(-drop).toLocaleString()}`}</text>}
            </g>
          );
        })}
      </svg>
      <figcaption className="mt-1 text-xs text-muted">Unique tokens after each step. Big drops mean the step removed many one-off words.</figcaption>
    </figure>
  );
}
