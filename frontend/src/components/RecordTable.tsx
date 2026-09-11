import { useEffect, useId, useState } from "react";

import { api } from "../api/client";
import type { RecordPage, RecordPair } from "../api/types";
import { useAsync } from "../hooks/useAsync";
import { Notice } from "./ui/Notice";
import { Skeleton } from "./ui/Skeleton";

const PAGE = 25;

interface Props { datasetId: string; runVersion: number; hasRun: boolean; onSelect: (pair: RecordPair) => void }

/** Original vs processed, server-paged. Cards on phones, a table from md up. */
export function RecordTable({ datasetId, runVersion, hasRun, onSelect }: Props) {
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState("");
  const [debounced, setDebounced] = useState("");
  const page = useAsync<RecordPage>();
  const searchId = useId();

  useEffect(() => { const t = setTimeout(() => setDebounced(search), 250); return () => clearTimeout(t); }, [search]);
  useEffect(() => { setOffset(0); }, [debounced, datasetId]);
  useEffect(() => { void page.run((signal) => api.records(datasetId, offset, PAGE, debounced, signal)); }, [datasetId, offset, debounced, runVersion, page.run]);

  const total = page.data?.total ?? 0, items = page.data?.items ?? [];
  const showSkeleton = page.loading && items.length === 0;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="font-semibold">Records</h3>
        <input aria-label="Search original text" id={searchId} className="field w-full sm:w-60" placeholder="Search text" value={search} onChange={(e) => setSearch(e.target.value)} />
      </div>
      <Notice error={page.error} />
      <p className="text-xs text-muted">Tap a record to see exactly which words changed.</p>

      <ul className="divide-y divide-rule md:hidden">
        {showSkeleton && Array.from({ length: 5 }, (_, i) => <li key={i} className="flex flex-col gap-2 py-3"><Skeleton /><Skeleton className="w-2/3" /></li>)}
        {items.map((p) => (
          <li key={p.original.id}>
            <button type="button" onClick={() => onSelect(p)} className="flex w-full flex-col gap-1 py-3 text-left">
              <span className="text-sm leading-snug">{p.original.text}</span>
              <span className="text-sm leading-snug text-accent">{p.processed ? p.processed.text || <em className="text-muted">empty after processing</em> : <em className="text-muted">{hasRun ? "dropped" : "not processed yet"}</em>}</span>
            </button>
          </li>
        ))}
      </ul>

      <table className="hidden w-full border-collapse text-sm md:table">
        <thead><tr className="text-left text-xs text-muted">
          <th className="border-b border-rule py-2 pr-3 font-medium">Original</th>
          <th className="border-b border-rule py-2 pr-3 font-medium">Processed</th>
          <th className="border-b border-rule py-2 text-right font-medium">Label</th>
        </tr></thead>
        <tbody>
          {showSkeleton && Array.from({ length: 8 }, (_, i) => (
            <tr key={i}><td className="border-b border-rule py-2.5 pr-3"><Skeleton className={i % 2 ? "w-4/5" : "w-full"} /></td><td className="border-b border-rule py-2.5 pr-3"><Skeleton className="w-3/5" /></td><td className="border-b border-rule py-2.5 text-right"><Skeleton className="w-8" /></td></tr>
          ))}
          {items.map((p) => (
            <tr key={p.original.id} tabIndex={0} onClick={() => onSelect(p)} onKeyDown={(e) => e.key === "Enter" && onSelect(p)} className="cursor-pointer hover:bg-surface-2 focus-visible:bg-surface-2">
              <td className="max-w-[46ch] border-b border-rule py-2.5 pr-3 align-top [overflow-wrap:anywhere]">{p.original.text}</td>
              <td className="max-w-[46ch] border-b border-rule py-2.5 pr-3 align-top [overflow-wrap:anywhere]">{p.processed ? p.processed.text || <em className="text-muted">empty after processing</em> : <span className="text-muted">{hasRun ? "dropped" : "—"}</span>}</td>
              <td className="tnum border-b border-rule py-2.5 text-right align-top text-muted">
                {p.original.label ?? ""}
                {p.original.label_source && <span className="ml-1 text-xs opacity-70">({p.original.label_source[0]})</span>}
              </td>
            </tr>
          ))}
          {!page.loading && items.length === 0 && <tr><td colSpan={3} className="py-3 text-muted">No records match.</td></tr>}
        </tbody>
      </table>

      <div className="flex items-center justify-between text-sm">
        <span className="tnum text-muted">{total ? `${offset + 1}–${Math.min(offset + PAGE, total)} of ${total.toLocaleString()}` : ""}</span>
        <span className="flex gap-2">
          <button type="button" className="btn" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
          <button type="button" className="btn" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>Next</button>
        </span>
      </div>
    </div>
  );
}
