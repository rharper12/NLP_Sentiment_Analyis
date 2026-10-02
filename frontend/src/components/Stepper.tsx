export type Stage = "collect" | "clean" | "analyze" | "label" | "export";
export const STAGES: { id: Stage; label: string; blurb: string }[] = [
  { id: "collect", label: "Collect", blurb: "Search a topic and gather posts" },
  {
    id: "clean",
    label: "Clean",
    blurb: "Choose how to clean and normalise the text",
  },
  { id: "analyze", label: "Analyze", blurb: "Run it and measure what changed" },
  {
    id: "label",
    label: "Label",
    blurb: "Comprehend first, then review by hand",
  },
  {
    id: "export",
    label: "Export",
    blurb: "Download or save the prepared data",
  },
];

const CONSUMER_STAGES = [
  STAGES[0],
  {
    id: "label" as const,
    label: "Review & label",
    blurb: "Keep and label, or exclude, in one pass",
  },
  STAGES[1],
  STAGES[2],
  STAGES[4],
];

/** Navigation and page titles share the same order for each collection workflow. */
export const stagesFor = (consumer = false) => (consumer ? CONSUMER_STAGES : STAGES);

interface Props {
  disabled?: boolean;
  consumer?: boolean;
  current: Stage;
  reached: Stage; // furthest stage the person may jump to
  onSelect: (stage: Stage) => void;
}

/** Show all five stages while keeping unavailable stages visible and disabled. */
export function Stepper({ current, reached, onSelect, disabled = false, consumer = false }: Props) {
  const stages = stagesFor(consumer);
  const reachedIndex = stages.findIndex((s) => s.id === reached);
  const currentIndex = stages.findIndex((s) => s.id === current);
  return (
    <nav aria-label="Progress" className="glass-bar border-b border-rule">
      <ol
        // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- The scroll region needs keyboard access while review disables its buttons.
        tabIndex={0}
        aria-label="Progress steps"
        className="focus-visible:-outline-offset-4 mx-auto flex max-w-6xl justify-between overflow-x-auto px-4 sm:px-6"
      >
        {stages.map((s, i) => {
          const done = i < currentIndex;
          const enabled = !disabled && i <= reachedIndex;
          const active = i === currentIndex;
          return (
            <li key={s.id} className="flex shrink-0 items-center">
              <button
                type="button"
                disabled={!enabled}
                onClick={() => onSelect(s.id)}
                aria-current={active ? "step" : undefined}
                className={`focus-visible:-outline-offset-4 group flex items-center gap-3 border-b-2 px-3 py-3 text-left sm:px-4 ${
                  active ? "border-accent" : "border-transparent"
                } disabled:cursor-default`}
              >
                <span
                  className={`tnum grid size-7 shrink-0 place-items-center rounded-full text-xs font-semibold ${
                    active
                      ? "bg-accent text-accent-ink ring-2 ring-accent ring-offset-2 ring-offset-surface"
                      : done
                        ? "border border-accent bg-accent-soft text-accent"
                        : "border border-rule text-muted"
                  }`}
                >
                  {done ? "✓" : i + 1}
                </span>
                <span className="flex flex-col">
                  <span
                    className={`text-sm font-semibold ${active ? "text-ink" : enabled ? "text-ink/80" : "text-muted"}`}
                  >
                    {s.label}
                  </span>
                  <span className="hidden text-xs text-muted 2xl:block">{s.blurb}</span>
                </span>
              </button>
              {i < stages.length - 1 && (
                <span className="mx-1 hidden h-px w-6 bg-rule sm:block" aria-hidden="true" />
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
