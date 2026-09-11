import { useEffect, useRef } from "react";

import type { RecordPair } from "../api/types";

/** Case-fold and strip punctuation so "Movie," matches the processed token "movie". */
const normalise = (w: string) => w.toLowerCase().replace(/[^\p{L}\p{N}']/gu, "");

/** Word-level diff: struck = removed by the pipeline, underlined = introduced (lemma, split contraction). */
export default function DiffDialog({ pair, onClose }: { pair: RecordPair; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { const d = ref.current; if (d && !d.open) d.showModal(); }, []);

  const originalWords = pair.original.text.split(/\s+/).filter(Boolean);
  const processedWords = pair.processed?.tokens ?? pair.processed?.text.split(/\s+/).filter(Boolean) ?? [];
  const remaining = new Map<string, number>();
  for (const w of processedWords) remaining.set(normalise(w), (remaining.get(normalise(w)) ?? 0) + 1);
  const marked = originalWords.map((w) => { const k = normalise(w), c = remaining.get(k) ?? 0; if (c > 0) { remaining.set(k, c - 1); return { w, kept: true }; } return { w, kept: false }; });
  const originalKeys = new Set(originalWords.map(normalise));
  const introduced = new Set(processedWords.filter((w) => !originalKeys.has(normalise(w))));

  return (
    <dialog ref={ref} onClose={onClose} aria-labelledby="diff-heading"
      className="glass-panel m-auto w-[min(720px,92vw)] p-5 text-ink shadow-2xl backdrop:bg-transparent sm:p-6">
      <div className="flex items-center justify-between gap-3">
        <h3 id="diff-heading" className="font-semibold">Record {pair.original.id}</h3>
        <button type="button" className="btn-link" onClick={onClose}>Close</button>
      </div>
      <h4 className="mt-4 text-xs font-medium text-muted">Original</h4>
      <p className="mt-1 leading-8">{marked.map((t, i) => <span key={i} className={t.kept ? "" : "text-removed line-through decoration-[1.5px]"}>{t.w} </span>)}</p>
      <h4 className="mt-4 text-xs font-medium text-muted">Processed</h4>
      {pair.processed ? (
        <p className="mt-1 leading-8">
          {processedWords.length === 0 && <em className="text-muted">empty after processing</em>}
          {processedWords.map((w, i) => <span key={i} className={introduced.has(w) ? "underline decoration-introduced decoration-[1.5px]" : ""}>{w} </span>)}
        </p>
      ) : <p className="mt-1 text-muted">This record was dropped by the pipeline.</p>}
      <p className="mt-4 text-xs text-muted"><s>struck</s> removed · <u>underlined</u> introduced (lemma or split contraction)</p>
    </dialog>
  );
}
