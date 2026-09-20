import { useId } from "react";

/** Presets cover the common cases; Custom exposes the two dates X's recent search accepts. */
type RangePreset = "7d" | "3d" | "24h" | "custom";

export interface TimeRange {
  preset: RangePreset;
  /** ISO dates (yyyy-mm-dd) from the date inputs; only meaningful when preset is "custom". */
  from: string;
  to: string;
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

/** The earliest date X will accept: recent search reaches back seven days. */
const earliestDate = (): string => isoDate(6);
export const today = (): string => isoDate(0);

/**
 * Resolve a range into the timestamps the API takes.
 *
 * Dates are sent as UTC instants: the start of the "from" day and the end of the "to" day, so a
 * range of 9th–13th includes everything posted on the 13th. The server clamps anything outside
 * what recent search can serve, so an out-of-range date degrades rather than failing.
 */
export function toWindow(range: TimeRange): { start?: string; end?: string } {
  if (range.preset === "custom") {
    return {
      start: range.from ? `${range.from}T00:00:00Z` : undefined,
      end: range.to ? `${range.to}T23:59:59Z` : undefined,
    };
  }
  const hours = range.preset === "24h" ? 24 : range.preset === "3d" ? 72 : 168;
  return { start: new Date(Date.now() - hours * 3_600_000).toISOString(), end: undefined };
}

/** True when a custom range is incomplete or the wrong way round. */
export function rangeError(range: TimeRange): string | null {
  if (range.preset !== "custom") return null;
  if (!range.from || !range.to) return "Choose both a start and an end date.";
  if (range.from > range.to) return "The start date must come before the end date.";
  if (range.from < earliestDate()) return "X only serves the last 7 days.";
  return null;
}

interface Props {
  value: TimeRange;
  onChange: (range: TimeRange) => void;
}

export function TimeRangePicker({ value, onChange }: Props) {
  const ids = { from: useId(), to: useId() };
  const error = rangeError(value);

  return (
    <fieldset className="flex flex-col gap-2 border-0 p-0">
      <legend className="mb-1 text-sm text-muted">Time range</legend>
      <div role="group" className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
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
      </div>

      {value.preset === "custom" && (
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col gap-1">
            <label htmlFor={ids.from} className="text-sm text-muted">From</label>
            <input
              id={ids.from}
              type="date"
              className="field w-44"
              min={earliestDate()}
              max={today()}
              value={value.from}
              onChange={(e) => onChange({ ...value, from: e.target.value })}
            />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor={ids.to} className="text-sm text-muted">To</label>
            <input
              id={ids.to}
              type="date"
              className="field w-44"
              min={value.from || earliestDate()}
              max={today()}
              value={value.to}
              onChange={(e) => onChange({ ...value, to: e.target.value })}
            />
          </div>
        </div>
      )}

      <p className={`text-xs ${error ? "text-warn-ink" : "text-muted"}`}>
        {error ?? "X serves the last 7 days only. Dates are treated as whole UTC days."}
      </p>
    </fieldset>
  );
}
