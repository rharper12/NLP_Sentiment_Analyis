import { useId } from "react";
import type { CollectionWindow } from "../../api/types";

/** Presets cover recent searches; Custom also supports the full archive. */
type RangePreset = "7d" | "3d" | "24h" | "custom";

export interface TimeRange {
  preset: RangePreset;
  /** ISO dates (yyyy-mm-dd) from the date inputs; only meaningful when preset is "custom". */
  from: string;
  to: string;
  timezone?: string;
}

export const DEFAULT_RANGE: TimeRange = { preset: "7d", from: "", to: "" };

const PRESETS: [RangePreset, string][] = [
  ["7d", "Last 7 days"],
  ["3d", "Last 3 days"],
  ["24h", "Last 24 hours"],
  ["custom", "Custom"],
];

/** yyyy-mm-dd for an offset from today, which is what <input type="date"> expects. */
const isoDate = (daysAgo: number): string =>
  new Date(Date.now() - daysAgo * 86_400_000).toISOString().slice(0, 10);

const ARCHIVE_START = "2006-03-01";
export const today = (): string => isoDate(0);

/**
 * Resolve a range into the timestamps the API takes.
 *
 * Calendar dates and an IANA zone go to the backend, which resolves the inclusive start and
 * exclusive next-day midnight with timezone rules. Relative presets remain UTC instants.
 */
export function toWindow(range: TimeRange): CollectionWindow {
  if (range.preset === "custom") {
    return {
      start_date: range.from,
      end_date: range.to,
      timezone: range.timezone ?? "UTC",
    };
  }
  const hours = range.preset === "24h" ? 24 : range.preset === "3d" ? 72 : 168;
  // Leave a minute for transit so the seven-day preset stays inside recent search's horizon.
  const margin = range.preset === "7d" ? 60_000 : 0;
  return { start: new Date(Date.now() - hours * 3_600_000 + margin).toISOString(), end: undefined };
}

/** True when a custom range is incomplete or the wrong way round. */
export function rangeError(range: TimeRange): string | null {
  if (range.preset !== "custom") return null;
  if (!range.from || !range.to) return "Choose both a start and an end date.";
  if (range.from > range.to) return "The start date must come before the end date.";
  if (range.from < ARCHIVE_START) return "X's searchable archive starts in March 2006.";
  try {
    const localToday = new Intl.DateTimeFormat("en-CA", { timeZone: range.timezone ?? "UTC", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
    if (range.to >= localToday) return "Choose completed days before today in the selected timezone.";
  }
  catch { return "Choose a valid timezone, such as America/Chicago."; }
  return null;
}

interface Props {
  value: TimeRange;
  onChange: (range: TimeRange) => void;
  calendarOnly?: boolean;
}

export function TimeRangePicker({ value, onChange, calendarOnly = false }: Props) {
  const ids = { from: useId(), to: useId(), zone: useId() };
  const error = rangeError(value);

  return (
    <fieldset className="flex flex-col gap-2 border-0 p-0">
      <legend className="mb-1 text-sm text-muted">Time range</legend>
      {!calendarOnly && <div role="group" className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
        {PRESETS.map(([preset, label]) => (
          <button
            key={preset}
            type="button"
            aria-pressed={value.preset === preset}
            onClick={() => onChange({ ...value, preset })}
            className="selectable px-3 py-2"
          >
            {label}
          </button>
        ))}
      </div>}

      {value.preset === "custom" && (
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col gap-1">
            <label htmlFor={ids.from} className="text-sm text-muted">From</label>
            <input
              id={ids.from}
              type="date"
              className="field w-44"
              min={ARCHIVE_START}
              max={today()}
              value={value.from}
              onChange={(e) => onChange({ ...value, from: e.target.value })}
            />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor={ids.zone} className="text-sm text-muted">Timezone</label>
            <input id={ids.zone} className="field" value={value.timezone ?? "UTC"} onChange={(e) => onChange({ ...value, timezone: e.target.value })} placeholder="America/Chicago" />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor={ids.to} className="text-sm text-muted">To</label>
            <input
              id={ids.to}
              type="date"
              className="field w-44"
              min={value.from || ARCHIVE_START}
              max={today()}
              value={value.to}
              onChange={(e) => onChange({ ...value, to: e.target.value })}
            />
          </div>
        </div>
      )}

      <p className={`text-xs ${error ? "text-warn-ink" : "text-muted"}`}>
        {error ?? "The final date is included through the next local midnight (exclusive). Choose completed days. Older than 7 days requires full-archive access; no recent-data fallback. Existing spend caps apply."}
      </p>
    </fieldset>
  );
}
