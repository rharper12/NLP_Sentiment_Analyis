import { useEffect, useMemo, useRef } from "react";

import type { RecordPair } from "../api/types";
import { textDiff, type DiffPart } from "./textDiff";

function HighlightedText({ parts, removed }: { parts: DiffPart[]; removed: boolean }) {
  return parts.map((part, index) => {
    if (!part.changed) return <span key={index}>{part.text}</span>;
    const whitespace = /^\s+$/u.test(part.text);
    const text = whitespace ? part.text.replace(/ /g, "·").replace(/\t/g, "⇥").replace(/\r/g, "␍").replace(/\n/g, "↵\n") : part.text;
    const title = whitespace ? `${removed ? "Removed" : "Added"} whitespace` : undefined;
    return removed
      ? <del key={index} title={title} className="rounded-sm bg-surface-2 text-removed decoration-[1.5px]">{text}</del>
      : <ins key={index} title={title} className="rounded-sm bg-surface-2 text-introduced decoration-[1.5px]">{text}</ins>;
  });
}

/** Word-level diff: struck = removed by the pipeline, underlined = introduced (lemma, split contraction). */
export default function DiffDialog({ pair, trigger, onClose }: { pair: RecordPair; trigger?: HTMLElement; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    const previous = trigger ?? (document.activeElement instanceof HTMLElement ? document.activeElement : null);
    if (d && !d.open) d.showModal();
    return () => { d?.close(); if (previous?.isConnected) previous.focus(); };
  }, [trigger]);

  const changes = useMemo(() => textDiff(pair.original.text, pair.processed?.text ?? ""), [pair.original.text, pair.processed?.text]);

  return (
    <dialog ref={ref} onCancel={(event) => {
      // Remove the selection synchronously, before another keyboard activation can reopen it.
      event.preventDefault(); onClose();
    }} onClose={(event) => {
      // Native close events can arrive after this dialog was removed and another opened.
      if (ref.current === event.currentTarget && !event.currentTarget.open) onClose();
    }} aria-labelledby="diff-heading"
      className="glass-panel m-auto w-[min(720px,92vw)] p-5 text-ink shadow-2xl backdrop:bg-transparent sm:p-6">
      <div className="flex items-center justify-between gap-3">
        <h3 id="diff-heading" className="font-semibold">Record {pair.original.id}</h3>
        <button type="button" className="btn-link" onClick={onClose}>Close</button>
      </div>
      <h4 className="mt-4 text-xs font-medium text-muted">Original</h4>
      <p className="mt-1 whitespace-pre-wrap break-words leading-8"><HighlightedText parts={changes.original} removed />{!pair.original.text && <em className="text-muted">empty original text</em>}</p>
      <h4 className="mt-4 text-xs font-medium text-muted">Processed</h4>
      {pair.processed ? (
        <p className="mt-1 whitespace-pre-wrap break-words leading-8">
          {!pair.processed.text && <em className="text-muted">empty after processing</em>}
          <HighlightedText parts={changes.processed} removed={false} />
        </p>
      ) : <p className="mt-1 text-muted">This record was dropped by the pipeline.</p>}
      <p className="mt-4 text-xs text-muted"><s>Struck through</s> = removed or replaced · <u>Underlined</u> = added or replacement text. Whitespace-only changes use · for spaces, ⇥ for tabs, ␍ for carriage returns and ↵ for line breaks.</p>
    </dialog>
  );
}
