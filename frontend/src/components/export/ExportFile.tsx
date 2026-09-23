import { useId, useState, type ReactNode } from "react";

interface Props {
  title: string;
  description: ReactNode;
  extension: string;
  defaultName: string;
  busy: boolean;
  disabled?: boolean;
  action?: "Download" | "Save";
  onExport: (filename?: string) => void;
}

/** Keep the extension outside the editable field; the server validates the stem again. */
export function ExportFile({ title, description, extension, defaultName, busy, disabled = false, action = "Download", onExport }: Props) {
  const id = useId();
  const [custom, setCustom] = useState(false);
  const [name, setName] = useState(defaultName);
  const value = custom ? name : defaultName;
  const valid = /^[A-Za-z0-9][A-Za-z0-9 _-]*$/.test(value.trim()) && value.trim().length <= 120;
  const invalid = custom && !valid;

  return (
    <li className="flex flex-col gap-3 p-5">
      <div><h3 className="font-semibold">{title}</h3><div className="text-sm text-muted">{description}</div></div>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={custom} disabled={busy} aria-label={`Custom filename for ${title}`} onChange={(event) => setCustom(event.target.checked)} />
        Custom filename
      </label>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
        <div className="min-w-0 flex-1">
          <label htmlFor={id} className="mb-1 block text-sm">{title} filename</label>
          <div className="flex items-center gap-2">
            <input id={id} type="text" className="field min-w-0 w-full" value={value} readOnly={!custom} disabled={busy} maxLength={120} spellCheck={false} autoComplete="off" aria-invalid={invalid} aria-describedby={`${id}-help${invalid ? ` ${id}-error` : ""}`} onChange={(event) => setName(event.target.value)} />
            <span className="shrink-0 text-sm">.{extension}</span>
          </div>
        </div>
        <button type="button" className="btn shrink-0" aria-label={action === "Save" ? title : `${action} ${title}`} disabled={disabled || busy || invalid} onClick={() => onExport(custom ? value.trim() : undefined)}>{busy ? `${action === "Save" ? "Saving" : "Downloading"}…` : action}</button>
      </div>
      <p id={`${id}-help`} className="text-xs text-muted">{custom ? "Use letters, numbers, spaces, hyphens or underscores; start with a letter or number. Up to 120 characters, without an extension." : "Uses the default name. Select Custom filename to edit it."} The .{extension} extension stays fixed.</p>
      {invalid && <p id={`${id}-error`} role="alert" className="text-sm text-error-ink">Enter a filename using the characters above. Do not include a dot or file extension.</p>}
    </li>
  );
}
