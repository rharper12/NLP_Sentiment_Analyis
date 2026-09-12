import { useCallback, useEffect, useRef, useState } from "react";

import { api, isAbort } from "../../api/client";
import { SENTIMENT_LABELS, type PostRecord, type SentimentLabel } from "../../api/types";
import { toError } from "../../hooks/useAsync";
import { SkeletonLines } from "../ui/Skeleton";

import { InfoIcon } from "./InfoIcon";
import { FLUSH_EVERY, KEYS, REVIEW_PAGE } from "./shared";

interface Props {
  datasetId: string;
  onError: (error: Error) => void;
  onDone: () => void;
}

/**
 * One post at a time with keyboard shortcuts. Decisions are buffered and flushed every
 * `FLUSH_EVERY` so a closed tab loses at most that many, and the review set is paged in as the
 * reviewer advances.
 */
export function Reviewer({ datasetId, onError, onDone }: Props) {
  const [items, setItems] = useState<PostRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [index, setIndex] = useState(0);
  const [decisions, setDecisions] = useState<{ [id: string]: SentimentLabel }>({});
  const pending = useRef<{ id: string; label: SentimentLabel }[]>([]);
  const [agree, setAgree] = useState({ agreed: 0, compared: 0 });
  const [saving, setSaving] = useState(false);

  // Highest offset already requested. Without this, any re-render while a page is in flight
  // (a save toggling `saving`, for instance) re-runs the effect and appends the same page twice.
  const requestedTo = useRef(-1);

  useEffect(() => {
    const needsPage = index >= items.length && items.length < total;
    const firstLoad = items.length === 0 && total === 0;
    if (!firstLoad && !needsPage) return;
    const offset = items.length;
    if (requestedTo.current >= offset) return;
    requestedTo.current = offset;

    const controller = new AbortController();
    api
      .reviewPage(datasetId, offset, REVIEW_PAGE, controller.signal)
      .then((page) => {
        setTotal(page.total);
        setItems((prev) => (prev.length === offset ? [...prev, ...page.items] : prev));
      })
      .catch((error) => {
        if (isAbort(error)) return;
        requestedTo.current = offset - 1; // allow a retry
        onError(toError(error));
      });
    return () => controller.abort();
  }, [index, items.length, total, datasetId, onError]);

  const flush = useCallback(async () => {
    if (pending.current.length === 0) return;
    const batch = pending.current; pending.current = [];
    setSaving(true);
    try { await api.manualLabels(datasetId, batch); } catch (e) { pending.current = [...batch, ...pending.current]; onError(toError(e)); } finally { setSaving(false); }
  }, [datasetId, onError]);

  const decide = useCallback((label: SentimentLabel) => {
    const item = items[index]; if (!item) return;
    setDecisions((d) => ({ ...d, [item.id]: label }));
    if (item.comprehend_label) setAgree((a) => ({ agreed: a.agreed + (item.comprehend_label === label ? 1 : 0), compared: a.compared + 1 }));
    pending.current.push({ id: item.id, label });
    if (pending.current.length >= FLUSH_EVERY) void flush();
    setIndex((i) => i + 1);
  }, [items, index, flush]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { const l = KEYS[e.key.toLowerCase()]; if (l && !e.metaKey && !e.ctrlKey) { e.preventDefault(); decide(l); } if (e.key === "ArrowLeft") setIndex((i) => Math.max(0, i - 1)); };
    window.addEventListener("keydown", onKey); return () => window.removeEventListener("keydown", onKey);
  }, [decide]);

  useEffect(() => () => { void flush(); }, [flush]);

  const finish = async () => { await flush(); onDone(); };
  const item = items[index];
  const finished = index >= total && total > 0;

  return (
    <div className="glass-panel flex flex-col gap-4 p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-semibold">Review</h3>
        <span className="tnum text-sm text-muted">{Math.min(index, total).toLocaleString()} of {total.toLocaleString()}{agree.compared > 0 && ` · agree with Comprehend ${Math.round((100 * agree.agreed) / agree.compared)}%`}{saving && " · saving…"}</span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded bg-surface-2"><div className="h-full bg-accent" style={{ width: `${total ? (100 * Math.min(index, total)) / total : 0}%` }} /></div>

      {!finished && !item && <SkeletonLines lines={3} />}
      {!finished && item && (
        <>
          <blockquote className="rounded-lg bg-surface-2 p-4 text-lg leading-relaxed">{item.text}</blockquote>
          <p className="flex items-start gap-1.5 text-xs text-muted">
            <InfoIcon />
            <span>Showing the original text for review — labels apply to the processed version used in Task 2.</span>
          </p>
          <div className="flex flex-wrap items-center gap-2 text-sm text-muted">
            {item.comprehend_label ? <span>Comprehend says <strong className="text-ink">{item.comprehend_label}</strong>{item.comprehend_confidence != null && <span className="tnum"> ({Math.round(item.comprehend_confidence * 100)}%)</span>}</span> : <span>No Comprehend label for this post.</span>}
            {decisions[item.id] && <span>· you said <strong className="text-ink">{decisions[item.id]}</strong></span>}
          </div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {SENTIMENT_LABELS.map((l, i) => (
              <button key={l} type="button" onClick={() => decide(l)} aria-pressed={decisions[item.id] === l} className="selectable flex items-center justify-between px-3.5 py-3 text-sm font-medium">
                <span className="capitalize">{l}{item.comprehend_label === l && decisions[item.id] !== l && <span className="ml-1 text-xs font-normal text-muted">(Comprehend)</span>}</span><kbd className="rounded bg-surface-2 px-1.5 text-xs text-muted">{i + 1}</kbd>
              </button>
            ))}
          </div>
          <p className="text-xs text-muted">Keys 1–4 or P / N / U / M. ← goes back one. Labels save every {FLUSH_EVERY} decisions and when you finish.</p>
        </>
      )}
      {finished && <p className="text-sm">All {total.toLocaleString()} reviewed.</p>}
      <div className="flex flex-wrap justify-between gap-3 border-t border-rule pt-4">
        <button type="button" className="btn" disabled={index === 0} onClick={() => setIndex((i) => i - 1)}>← Previous</button>
        <button type="button" className="btn-primary" onClick={() => void finish()}>{finished ? "Finish" : "Stop here and keep labels"}</button>
      </div>
    </div>
  );
}
