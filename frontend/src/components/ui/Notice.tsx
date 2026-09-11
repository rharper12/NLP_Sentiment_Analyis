import { ApiError } from "../../api/client";

interface Props {
  error?: Error | null;
  warnings?: string[];
  className?: string;
}

/** Errors quote the request id so log lines can be found; warnings are informational. */
export function Notice({ error, warnings = [], className = "" }: Props) {
  if (!error && warnings.length === 0) return null;
  return (
    <div className={`flex flex-col gap-2 ${className}`}>
      {error && (
        <div className="rounded-md bg-error-bg px-3 py-2.5 text-sm text-error-ink" role="alert">
          <strong className="font-semibold">Request failed.</strong> {error.message}
          {error instanceof ApiError && error.requestId && (
            <span className="tnum mt-1 block text-xs opacity-80">request {error.requestId}</span>
          )}
        </div>
      )}
      {warnings.map((w) => (
        <div key={w} className="rounded-md bg-warn-bg px-3 py-2.5 text-sm text-warn-ink">{w}</div>
      ))}
    </div>
  );
}
