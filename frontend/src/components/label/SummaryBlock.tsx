import type { LabelSummary } from "../../api/types";

/** One colour per label so the stacked bar reads the same way every time. */
const LABEL_BAR: { [label: string]: string } = {
  positive: "var(--color-introduced)",
  negative: "var(--color-removed)",
  neutral: "var(--color-muted)",
  mixed: "var(--color-accent)",
};

/** Distribution and provenance as two labelled sections, with a stacked bar for the split. */
export function SummaryBlock({ s }: { s: LabelSummary }) {
  const pct = (n: number) => (s.total ? (100 * n) / s.total : 0);
  const distribution = Object.entries(s.by_label).sort((a, b) => b[1] - a[1]);
  const provenance = Object.entries(s.by_source).sort((a, b) => b[1] - a[1]);

  const rows = (entries: [string, number][], total: number) => (
    <ul className="mt-2 flex flex-col divide-y divide-rule text-sm">
      {entries.map(([name, count]) => (
        <li key={name} className="flex items-baseline justify-between gap-4 py-1.5">
          <span className="capitalize">{name}</span>
          <span className="tnum tabular-nums text-right">
            {count.toLocaleString()}
            <span className="ml-2 inline-block w-12 text-muted">
              {total ? `${((100 * count) / total).toFixed(0)}%` : ""}
            </span>
          </span>
        </li>
      ))}
    </ul>
  );

  return (
    <div className="flex flex-col gap-5">
      <section aria-labelledby="dist-heading">
        <h4 id="dist-heading" className="text-xs font-semibold uppercase tracking-wide text-muted">Distribution</h4>
        <div className="mt-2 flex h-3 w-full overflow-hidden rounded-full border border-rule" role="img"
          aria-label={distribution.map(([k, v]) => `${k} ${Math.round(pct(v))}%`).join(", ")}>
          {distribution.map(([label, count]) => (
            <span key={label} title={`${label}: ${count.toLocaleString()} (${Math.round(pct(count))}%)`}
              style={{ width: `${pct(count)}%`, background: LABEL_BAR[label] ?? "var(--color-accent)" }} />
          ))}
        </div>
        {rows(distribution, s.total)}
      </section>

      <section aria-labelledby="prov-heading" className="border-t border-rule pt-4">
        <h4 id="prov-heading" className="text-xs font-semibold uppercase tracking-wide text-muted">Provenance</h4>
        {rows(provenance, s.total)}
        {s.manual_vs_comprehend_agreement != null && (
          <p className="mt-3 text-sm">
            Reviewers agreed with Comprehend on{" "}
            <strong className="tnum">{(s.manual_vs_comprehend_agreement * 100).toFixed(1)}%</strong>{" "}
            of {s.comparable_records.toLocaleString()} comparable posts across all manual labels ({s.disagreements} disagreements). This describes reviewed posts only, not overall accuracy. Prioritizing low-confidence posts makes this a targeted error check.
          </p>
        )}
      </section>
    </div>
  );
}
