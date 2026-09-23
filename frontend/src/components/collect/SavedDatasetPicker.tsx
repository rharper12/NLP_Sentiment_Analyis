import { useEffect, useId, useState } from "react";

import { api } from "../../api/client";
import type { LocalDatasetPage } from "../../api/types";
import { useAsync } from "../../hooks/useAsync";

interface Props {
  disabled: boolean;
  selected: string;
  onSelect: (datasetId: string) => void;
}

/** Fetch small metadata pages; the selected JSON is validated when the user opens it. */
export function SavedDatasetPicker({ disabled, selected, onSelect }: Props) {
  const id = useId();
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [files, setFiles] = useState<LocalDatasetPage["items"]>([]);
  const page = useAsync<LocalDatasetPage>();
  const { run, cancel } = page;
  const selectedFile = files.find((file) => file.dataset_id === selected);

  useEffect(() => {
    void run((signal) => api.localDatasets(offset, signal)).then((result) => {
      if (result) setFiles((previous) => offset === 0 ? result.items : [...new Map([...previous, ...result.items].map((item) => [item.dataset_id, item])).values()]);
    });
    return cancel;
  }, [offset, revision, run, cancel]);

  return (
    <div className="flex flex-col gap-3">
      <div>
        <h3 className="font-semibold">Start again from a saved dataset</h3>
        <p className="mt-1 text-sm text-muted">Local development only. Choose a JSON saved by this app, ordered by its latest save time, newest first.</p>
      </div>
      <label htmlFor={id} className="text-sm font-medium">Saved dataset</label>
      <select id={id} className="field w-full min-w-0" value={selected} disabled={disabled || !files.length} aria-describedby={`${id}-help`} onChange={(event) => onSelect(event.target.value)}>
        <option value="">{page.loading && !files.length ? "Loading saved datasets…" : "Choose a saved dataset"}</option>
        {files.map((file) => <option key={file.dataset_id} value={file.dataset_id}>{new Date(file.modified_at).toLocaleString()} — {file.filename}</option>)}
      </select>
      {selectedFile && <p className="break-all text-xs text-muted">{selectedFile.filename} · {(selectedFile.bytes / 1024).toFixed(1)} KiB</p>}
      <p id={`${id}-help`} className="text-sm text-muted">Opens Clean with a new copy of the original posts. Previous cleaning and manual or Comprehend labels are reset; labels from the original source are kept. The saved file stays intact.</p>
      <p role="status" className="text-sm text-muted">
        {page.loading ? "Loading saved datasets…" : page.data && !files.length ? "No saved datasets yet. Collect posts or import a CSV first; the app saves a JSON automatically." : page.data ? `${files.length} of ${page.data.total} saved files shown.` : ""}
      </p>
      {page.error && <div role="alert" className="text-sm text-error-ink"><p>{page.error.message}</p><button type="button" className="btn mt-2" disabled={disabled || page.loading} onClick={() => setRevision((value) => value + 1)}>Retry loading saved datasets</button></div>}
      <div className="flex flex-wrap gap-2">
        <button type="button" className="btn" disabled={disabled || page.loading} onClick={() => { onSelect(""); setOffset(0); setRevision((value) => value + 1); }}>Refresh saved datasets</button>
        {page.data && !page.error && files.length < page.data.total && <button type="button" className="btn" disabled={disabled || page.loading} onClick={() => setOffset((value) => value + 50)}>Load older datasets</button>}
      </div>
    </div>
  );
}
