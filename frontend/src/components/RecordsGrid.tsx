import {
  AllCommunityModule,
  ModuleRegistry,
  colorSchemeDark,
  colorSchemeLight,
  themeQuartz,
  type ColDef,
  type GridApi,
  type GridReadyEvent,
  type ICellRendererParams,
} from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import { useCallback, useMemo, useRef, useState } from "react";

import { spreadsheetText } from "../export/spreadsheet";

import type { LabelSource, RecordPair } from "../api/types";
import type { Theme } from "../hooks/useTheme";

// Community modules only: sorting, filtering, pagination, cell rendering, CSV export, selection.
// No enterprise package is installed, so nothing here can accidentally depend on one.
ModuleRegistry.registerModules([AllCommunityModule]);

/** Grid theme built from the app's own tokens so the table never looks pasted in. */
const gridTheme = themeQuartz.withParams({
  fontFamily: "inherit",
  backgroundColor: "transparent",
  foregroundColor: "var(--color-ink)",
  borderColor: "var(--color-rule)",
  headerTextColor: "var(--color-muted)",
  headerBackgroundColor: "transparent",
  rowHoverColor: "var(--color-surface-2)",
  selectedRowBackgroundColor: "var(--color-accent-soft)",
  accentColor: "var(--color-accent)",
  wrapperBorder: false,
  headerRowBorder: { color: "var(--color-rule)" },
  spacing: 7,
});

/** Flat row shape for the grid. Fields are nullable because the schema marks them optional. */
interface Row {
  id: string;
  original: string;
  processed: string | null;
  label: string | null;
  labelSource: LabelSource | null;
  confidence: number | null;
}

interface Props {
  pairs: RecordPair[];
  theme: Theme;
  hasRun: boolean;
  onSelect: (pair: RecordPair, trigger?: HTMLElement) => void;
}

const SOURCE_TITLE: { [key in LabelSource]: string } = {
  source: "label came with the dataset",
  comprehend: "labelled by Amazon Comprehend",
  manual: "labelled by a reviewer",
};

/** Label plus a provenance badge, so where a label came from is visible while scanning. */
function LabelCell({ data }: ICellRendererParams<Row>) {
  if (!data?.label) return <span className="text-muted">—</span>;
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="capitalize">{data.label}</span>
      {data.labelSource && (
        <span
          title={SOURCE_TITLE[data.labelSource]}
          className="rounded border border-rule px-1 text-[11px] uppercase tracking-wide text-muted"
        >
          {data.labelSource.slice(0, 3)}
        </span>
      )}
    </span>
  );
}

/**
 * Records table. AG Grid Community with the client-side row model: every record is already in
 * memory, so the grid sorts, filters and paginates without another request and virtualises rows
 * for smooth scrolling. Row height is dynamic because posts wrap to two or three lines.
 */
export function RecordsGrid({ pairs, theme, hasRun, onSelect }: Props) {
  const api = useRef<GridApi<Row> | null>(null);
  const [quickFilter, setQuickFilter] = useState("");

  const rows = useMemo<Row[]>(
    () =>
      pairs.map((p) => ({
        id: p.original.id,
        original: p.original.text,
        processed: p.processed ? p.processed.text : null,
        label: p.original.label ?? null,
        labelSource: p.original.label_source ?? null,
        confidence: p.original.label_confidence ?? null,
      })),
    [pairs],
  );

  const columns = useMemo<ColDef<Row>[]>(
    () => [
      {
        colId: "inspect",
        headerName: "Inspect",
        width: 140,
        pinned: "left",
        sortable: false,
        filter: false,
        resizable: false,
        cellRenderer: ({ data }: ICellRendererParams<Row>) => data && (
          <button type="button" className="btn record-diff-action my-1 py-1"
            aria-label={`View changes for record ${data.id}`}
            onClick={(event) => {
              event.stopPropagation();
              const pair = pairs.find((p) => p.original.id === data.id);
              if (pair) onSelect(pair, event.currentTarget);
            }}>View changes</button>
        ),
        // Let the native button receive activation and Tab from its containing grid cell.
        // Other keys retain AG Grid's normal row/column navigation.
        suppressKeyboardEvent: ({ event }) => {
          const target = event.target;
          const cell = target instanceof HTMLElement ? target.closest(".ag-cell") : null;
          const button = cell?.querySelector("button");
          if (event.key === "Tab" && !event.shiftKey && target === cell && button) {
            event.preventDefault(); button.focus(); return true;
          }
          if (event.key === "Tab" && event.shiftKey && target === button && cell instanceof HTMLElement) {
            event.preventDefault(); cell.focus(); return true;
          }
          return target === button && (event.key === "Enter" || event.key === " ");
        },
      },
      {
        field: "original",
        headerName: "Original",
        flex: 3,
        minWidth: 260,
        wrapText: true,
        autoHeight: true,
        filter: "agTextColumnFilter",
      },
      {
        field: "processed",
        headerName: hasRun ? "Processed" : "Processed (run the pipeline)",
        flex: 3,
        minWidth: 260,
        wrapText: true,
        autoHeight: true,
        filter: "agTextColumnFilter",
        valueFormatter: (p) => p.value ?? (hasRun ? "dropped" : "—"),
        cellClass: (p) => (p.value ? "" : "text-muted"),
      },
      {
        field: "label",
        headerName: "Label",
        width: 150,
        filter: "agTextColumnFilter",
        cellRenderer: LabelCell,
      },
      {
        field: "confidence",
        headerName: "Conf.",
        width: 100,
        type: "numericColumn",
        valueFormatter: (p) => (p.value == null ? "" : `${Math.round(p.value * 100)}%`),
      },
    ],
    [hasRun, pairs, onSelect],
  );

  const onGridReady = useCallback((event: GridReadyEvent<Row>) => {
    api.current = event.api;
  }, []);

  const exportCsv = useCallback(() => {
    // Exports exactly what is on screen (current sort, filter and columns), which is what a
    // person expects from a grid button; the Export stage is the place for the full dataset.
    api.current?.exportDataAsCsv({
      fileName: "records-view.csv",
      columnKeys: ["original", "processed", "label", "confidence"],
      processCellCallback: (cell) => spreadsheetText(cell.formatValue(cell.value) ?? String(cell.value ?? "")),
    });
  }, []);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="font-semibold">Records</h3>
          <p className="text-xs text-muted">
            Use View changes or click a row to inspect changed words. Sort and filter from the column menus.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="sr-only" htmlFor="records-search">
            Search records
          </label>
          <input
            id="records-search"
            className="field w-full sm:w-56"
            placeholder="Search all columns"
            value={quickFilter}
            onChange={(e) => setQuickFilter(e.target.value)}
          />
          <button type="button" className="btn" onClick={exportCsv}>
            Export view
          </button>
        </div>
      </div>

      {/* Fixed height: the grid virtualises rows inside it, so 600 records scroll smoothly
          without stretching the page to thousands of pixels. */}
      <div className="h-[32rem]">
        <AgGridReact<Row>
          theme={gridTheme.withPart(theme === "dark" ? colorSchemeDark : colorSchemeLight)}
          rowData={rows}
          columnDefs={columns}
          defaultColDef={{ sortable: true, filter: true, resizable: true }}
          quickFilterText={quickFilter}
          getRowId={(p) => p.data.id}
          pagination
          paginationPageSize={25}
          paginationPageSizeSelector={[25, 50, 100]}
          rowSelection={{ mode: "singleRow", checkboxes: false, enableClickSelection: true }}
          onGridReady={onGridReady}
          onRowClicked={(e) => {
            // AG Grid's native listener can run before React's stopPropagation.
            const target = e.event?.target;
            if (target instanceof HTMLElement && target.closest(".record-diff-action")) return;
            const pair = pairs.find((p) => p.original.id === e.data?.id);
            if (pair) {
              const row = target instanceof HTMLElement ? target.closest(".ag-row") : null;
              const trigger = row?.querySelector<HTMLElement>("button") ?? undefined;
              onSelect(pair, trigger);
            }
          }}
          overlayNoRowsTemplate="No records match."
        />
      </div>
    </div>
  );
}
