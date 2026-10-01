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
    <div className="flex flex-col gap-3">
      <p role="status" className="font-medium">
        {counts.reviewed_final} of {counts.reviewed_target} final reviewed examples ·{" "}
        {counts.shortfall ? `Partial: ${counts.shortfall} still needed` : "Reviewed target reached"}
      </p>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
        {entries.map(([label, value]) => (
          <div key={label}>
            <dt className="text-muted">{label}</dt>
            <dd className="tnum">{value}</dd>
          </div>
        ))}
      </dl>
      <p className="text-xs text-muted">
        {counts.author_cap_held} human inclusions held by the author limit. Earliest timestamp, then
        record ID decides selection; sentiment and engagement are unused. {counts.missing_author}{" "}
        candidates lack an author ID, so their author limit cannot be enforced.
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
    </div>
  );
}
