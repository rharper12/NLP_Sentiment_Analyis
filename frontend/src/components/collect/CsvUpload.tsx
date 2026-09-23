import { useId, useRef, useState, type DragEvent } from "react";

import { api } from "../../api/client";
import type { CsvValidation } from "../../api/types";
import { useAsync } from "../../hooks/useAsync";

// Matches the server's multipart file limit; content rules stay in its CSV parser.
const MAX_BYTES = 4 * 1024 * 1024;

interface Props {
  disabled: boolean;
  onValidated: (file: File | null) => void;
}

/** Selection stays provisional until the server validates the entire CSV without storing it. */
export function CsvUpload({ disabled, onValidated }: Props) {
  const id = useId();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [selectionError, setSelectionError] = useState<string | null>(null);
  const validation = useAsync<CsvValidation>();

  const select = async (files: File[]) => {
    if (disabled) return;
    onValidated(null);
    validation.reset();
    setSelectionError(null);
    setFile(null);
    if (input.current) input.current.value = "";
    if (files.length !== 1) { setSelectionError("Choose one CSV file at a time."); return; }
    const selected = files[0];
    if (!selected.name.toLowerCase().endsWith(".csv")) { setSelectionError("Choose a .csv file saved with UTF-8 encoding."); return; }
    if (selected.size === 0) { setSelectionError("This file is empty. Include a text header and at least one text row."); return; }
    if (selected.size > MAX_BYTES) { setSelectionError("This file exceeds 4 MiB. Choose a smaller CSV."); return; }
    setFile(selected);
    const result = await validation.run((signal) => api.validateCsv(selected, signal));
    if (result) onValidated(selected);
  };

  const drop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    void select(Array.from(event.dataTransfer.files));
  };
  const error = selectionError ?? validation.error?.message;
  const checked = !validation.loading && !error ? validation.data : null;

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h3 className="font-semibold">Upload your CSV</h3>
        <p className="mt-1 text-sm text-muted">Choose a file to check its format and preview the posts before importing.</p>
      </div>
      <div
        onDragOver={(event) => { event.preventDefault(); if (!disabled) setDragging(true); }}
        onDragLeave={(event) => { if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) setDragging(false); }}
        onDrop={drop}
        className={`rounded-xl border-2 border-dashed p-6 text-center transition-colors ${dragging ? "border-accent bg-accent-soft" : "border-rule bg-surface-2"}`}
      >
        <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="mx-auto mb-3 size-8 text-muted"><path d="M12 16V4m-4 4 4-4 4 4M4 15v5h16v-5" /></svg>
        <p className="font-medium">{dragging ? "Drop your CSV here" : "Drag and drop a CSV file here"}</p>
        <p className="mb-4 mt-1 text-sm text-muted">or browse files on your device</p>
        <input ref={input} id={id} type="file" accept=".csv,text/csv" disabled={disabled} className="sr-only" tabIndex={-1} aria-label="CSV file" onChange={(event) => { if (event.target.files?.length) void select(Array.from(event.target.files)); }} />
        <button type="button" className="btn" disabled={disabled} aria-describedby={`${id}-requirements`} onClick={() => input.current?.click()}>{file ? "Choose another CSV" : "Choose CSV file"}</button>
        <p className="mt-3 text-xs text-muted">UTF-8 · Up to 4 MiB · Up to 5,000 data rows</p>
      </div>

      <div id={`${id}-requirements`} className="text-sm">
        <p><strong>Required:</strong> a <code>text</code> column with one post per row.</p>
        <p className="mt-1 text-muted"><strong>Optional:</strong> <code>id</code> for unique record IDs and <code>label</code> for existing labels. Blank IDs are generated. Other columns are ignored.</p>
        <details className="mt-2 text-muted">
          <summary className="cursor-pointer text-ink">CSV example and formatting rules</summary>
          <pre className="mt-2 whitespace-pre-wrap break-words rounded-md bg-surface-2 p-3 text-xs">{'text,id,label\n"I love this phone!",post-1,positive\n"Good camera, short battery life",post-2,mixed'}</pre>
          <p className="mt-2">Use commas between columns. Quote text containing commas, line breaks, or quotes; double any quote inside quoted text. Headers ignore case and surrounding spaces. Blank text rows are skipped. Existing labels are kept and lowercased; standard sentiment labels are positive, negative, neutral, and mixed.</p>
        </details>
      </div>

      {file && <p className="break-all text-sm"><strong>{file.name}</strong> <span className="text-muted">({(file.size / 1024).toFixed(1)} KiB)</span></p>}
      <div role="status" aria-live="polite" className="text-sm">
        {validation.loading && "Checking encoding, columns, IDs, and all rows…"}
        {checked && <>
          <p className="font-medium">CSV validated — {checked.record_count.toLocaleString()} non-empty rows ready to import.</p>
          <p className="mt-1 text-muted">{checked.labelled_count.toLocaleString()} rows have existing labels. Duplicate posts may be removed during import.</p>
          {checked.skipped_empty > 0 && <p className="mt-1 text-warn-ink">{checked.skipped_empty} blank text rows will be skipped.</p>}
          {checked.record_count < 500 && <p className="mt-1 text-warn-ink">Your assignment needs at least 500 posts. This file has fewer; you can still import it.</p>}
        </>}
      </div>
      {error && <p role="alert" className="text-sm text-error-ink">{error}</p>}
      {validation.error && file && <button type="button" className="btn self-start" disabled={disabled} onClick={() => void select([file])}>Check file again</button>}
      {checked && <div className="rounded-lg border border-rule p-4">
        <h4 className="text-sm font-semibold">Preview · first {checked.preview.length} posts</h4>
        <ul className="mt-2 divide-y divide-rule text-sm">{checked.preview.map((record) => <li key={record.id} className="break-words py-2"><p className="whitespace-pre-wrap">{record.text.slice(0, 300)}{record.text.length > 300 ? "…" : ""}</p>{record.label && <p className="mt-1 text-xs text-muted">Label: {record.label}</p>}</li>)}</ul>
      </div>}
    </div>
  );
}
