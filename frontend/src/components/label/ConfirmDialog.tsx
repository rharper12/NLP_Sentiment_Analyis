import { useEffect, useRef } from "react";

import type { LabelEstimate } from "../../api/types";

import { PRICING_URL, SLICE, usd } from "./shared";

export function ConfirmDialog({ est, onCancel, onConfirm }: { est: LabelEstimate; onCancel: () => void; onConfirm: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { ref.current?.showModal(); }, []);
  return (
    <dialog ref={ref} onClose={onCancel} aria-labelledby="confirm-heading" className="glass-panel m-auto w-[min(480px,92vw)] p-6 text-ink shadow-2xl backdrop:bg-transparent">
      <h3 id="confirm-heading" className="font-semibold">Send {est.records_to_send.toLocaleString()} posts to Amazon Comprehend?</h3>
      <dl className="tnum mt-4 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
        <dt className="text-muted">Billable units</dt><dd>{est.billable_units.toLocaleString()}</dd>
        <dt className="text-muted">Rate</dt><dd>{est.cost_per_unit_usd != null ? `$${est.cost_per_unit_usd} per unit (${est.unit_chars} chars, min ${est.min_units_per_document})` : "unavailable right now"}</dd>
        <dt className="text-muted">Estimated charge</dt><dd className="text-lg font-semibold">{est.estimated_cost_usd != null ? usd(est.estimated_cost_usd) : "—"}</dd>
      </dl>
      {est.estimated_cost_usd == null && (
        <p className="mt-3 text-sm text-warn-ink">No current price could be fetched. Check <a className="underline" href={PRICING_URL} target="_blank" rel="noreferrer">AWS's Comprehend pricing</a> and estimate the charge yourself ({est.billable_units.toLocaleString()} units) before confirming.</p>
      )}
      <p className="mt-3 text-xs text-muted">Charged to the AWS account the server runs under. Labels are saved after every batch of {SLICE}; you can cancel at any time and keep what has been labelled.</p>
      <div className="mt-5 flex justify-end gap-2"><button type="button" className="btn" onClick={onCancel}>Cancel</button><button type="button" className="btn-primary" onClick={onConfirm}>Confirm and label</button></div>
    </dialog>
  );
}
