import { useEffect, useId, useState } from "react";

import { api } from "../api/client";
import type { SpendSummary } from "../api/types";
import { useAsync } from "../hooks/useAsync";
import { Skeleton } from "./ui/Skeleton";

const usd = (n: number) => `$${n.toFixed(2)}`;

/** Always-visible X spend. Opens to caps, month total, and an optional live check with X. */
export function SpendChip({ refreshKey }: { refreshKey: number }) {
  const spend = useAsync<SpendSummary>();
  const { run: loadSpend } = spend;
  const [open, setOpen] = useState(false);
  const [withX, setWithX] = useState(false);
  const panelId = useId();

  useEffect(() => {
    void loadSpend((signal) => api.spend(withX, signal));
  }, [refreshKey, withX, loadSpend]);

  const s = spend.data;
  return (
    <div className="relative">
      <button
        type="button"
        className="tnum inline-flex items-baseline gap-2 rounded-full border border-rule bg-surface px-3 py-1.5 text-xs hover:bg-surface-2 sm:text-sm"
        aria-expanded={open} aria-controls={panelId} onClick={() => setOpen((o) => !o)} title="X API spend today"
      >
        {s ? (
          <>
            <span className="hidden text-muted sm:inline">X spend today</span>
            <span className="text-muted sm:hidden">X</span>
            <strong className="font-semibold">{usd(s.today_cost_usd)}</strong>
            <span className="hidden text-muted md:inline">{s.today_reads.toLocaleString()} reads</span>
          </>
        ) : <Skeleton className="w-24" />}
      </button>
      {open && s && (
        <div id={panelId} role="dialog" aria-label="X spend details"
          className="glass-panel absolute right-0 top-[calc(100%+8px)] z-20 flex w-[min(340px,90vw)] flex-col gap-3 p-4 shadow-2xl">
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
            <dt className="text-muted">Today</dt><dd className="tnum">{s.today_reads.toLocaleString()} reads · {usd(s.today_cost_usd)}</dd>
            <dt className="text-muted">This month</dt><dd className="tnum">{s.month_reads.toLocaleString()} reads · {usd(s.month_cost_usd)}</dd>
            <dt className="text-muted">Left today</dt><dd className="tnum">{s.remaining_today.toLocaleString()} of {s.cap_per_day.toLocaleString()}</dd>
            <dt className="text-muted">Per search cap</dt><dd className="tnum">{s.cap_per_fetch.toLocaleString()} reads</dd>
            <dt className="text-muted">Rate</dt><dd className="tnum">{usd(s.cost_per_read_usd * 1000)} per 1,000</dd>
          </dl>
          {s.x_configured ? (
            <>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" className="accent-accent" checked={withX} onChange={(e) => setWithX(e.target.checked)} />
                Also ask X for its own usage figure
              </label>
              {withX && <p className="text-xs text-muted">{s.x_usage ? `X reports: ${JSON.stringify(s.x_usage)}` : "X did not return usage for this account tier; the local ledger is authoritative."}</p>}
            </>
          ) : <p className="text-xs text-muted">No X token configured on the server. Set X_BEARER_TOKEN to enable searches.</p>}
          <p className="text-xs text-muted">Counted locally from every billed read. The caps stop a search before it exceeds them.</p>
        </div>
      )}
    </div>
  );
}
