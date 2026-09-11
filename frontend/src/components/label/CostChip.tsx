import type { LabelEstimate } from "../../api/types";

import { PRICING_URL, usd } from "./shared";

/** The estimate, or an honest "unavailable" when no current rate could be fetched. */
export function CostChip({ est }: { est: LabelEstimate }) {
  if (est.estimated_cost_usd == null) {
    return (
      <div className="rounded-lg bg-warn-bg px-3 py-2 text-sm text-warn-ink">
        <strong className="font-semibold">Estimate unavailable right now.</strong> Check{" "}
        <a className="underline" href={PRICING_URL} target="_blank" rel="noreferrer">AWS's current Comprehend pricing</a>{" "}
        and estimate the cost manually before proceeding: this job is {est.billable_units.toLocaleString()} billable units for {est.records_to_send.toLocaleString()} posts.
      </div>
    );
  }
  const freshness = est.price_status === "live" ? "live price" : est.price_status === "cached" ? "price cached today" : "price may be out of date";
  return (
    <div className="tnum inline-flex w-fit flex-wrap items-baseline gap-2 rounded-full border border-rule bg-surface-2 px-3 py-1.5 text-sm">
      <span className="text-muted">Estimated cost</span><strong>{usd(est.estimated_cost_usd)}</strong>
      <span className="text-xs text-muted">{est.records_to_send.toLocaleString()} posts · {est.billable_units.toLocaleString()} units · ${est.cost_per_unit_usd}/unit · {freshness}</span>
    </div>
  );
}
