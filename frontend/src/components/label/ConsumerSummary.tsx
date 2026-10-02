import type { ConsumerCounts } from "../../api/types";

/** Shared live counts keep candidates, decisions, and usable examples distinct. */
export function ConsumerSummary({
  counts,
  timezone,
}: {
  counts: ConsumerCounts;
  timezone: string;
}) {
  const entries = [
    ["Retrieved records / provider reads", counts.retrieved],
    ["Unique provider IDs", counts.unique_records],
    ["Screened candidates", counts.screened_candidates],
    ["Pending eligibility", counts.pending_eligibility],
    ["Human-reviewed inclusions", counts.human_inclusions],
    ["Human-reviewed exclusions", counts.human_exclusions],
    ["Included after author limit", counts.included],
    ["Awaiting sentiment review", counts.pending_sentiment],
  ] as const;
  return (
    <div className="rounded-xl border border-rule bg-surface-2 p-4 sm:p-5">
      <p role="status" className="font-medium">
        {counts.reviewed_final} of {counts.reviewed_target} final reviewed examples ·{" "}
        {counts.shortfall ? `Partial: ${counts.shortfall} still needed` : "Reviewed target reached"}
      </p>
      <dl className="mt-4 grid grid-cols-3 gap-3">
        {(
          [
            ["Reviewed & kept", counts.reviewed_final],
            ["Needs review", counts.pending_eligibility + counts.pending_sentiment],
            ["Excluded", counts.human_exclusions],
          ] as const
        ).map(([label, value]) => (
          <div key={label}>
            <dt className="text-xs text-muted">{label}</dt>
            <dd className="tnum mt-1 text-2xl font-semibold">{value.toLocaleString()}</dd>
          </div>
        ))}
      </dl>
      <details className="mt-4 border-t border-rule pt-3 text-sm">
        <summary className="cursor-pointer text-muted">
          Collection details &amp; date coverage
        </summary>
        <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
          {entries.map(([label, value]) => (
            <div key={label}>
              <dt className="text-muted">{label}</dt>
              <dd className="tnum">{value}</dd>
            </div>
          ))}
        </dl>
        <p className="my-3 text-xs text-muted">
          {counts.author_cap_held} human inclusions held by the author limit. Earliest timestamp,
          then record ID decides selection; sentiment and engagement are unused.{" "}
          {counts.missing_author} candidates lack an author ID, so their author limit cannot be
          enforced.
        </p>
        <table className="w-full text-left text-sm">
          <caption className="mb-2 text-left text-muted">Actual date coverage ({timezone})</caption>
          <thead>
            <tr>
              <th scope="col">Day</th>
              <th scope="col">Candidates</th>
              <th scope="col">Included</th>
              <th scope="col">Fully reviewed</th>
            </tr>
          </thead>
          <tbody>
            {counts.days.map((day) => (
              <tr key={day.day}>
                <th scope="row" className="py-1 font-normal">
                  {day.day}
                </th>
                <td>{day.candidates ?? 0}</td>
                <td>{day.included ?? 0}</td>
                <td>{day.reviewed_final ?? 0}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}
