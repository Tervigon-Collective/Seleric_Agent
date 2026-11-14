import { useMemo, useState } from "react";
import { flexRender, getCoreRowModel, getSortedRowModel, useReactTable, type ColumnDef, type SortingState } from "@tanstack/react-table";
import { DownloadIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

type Row = Record<string, unknown>;

const cellText = (value: unknown) => typeof value === "string" ? value : JSON.stringify(value, null, 2);

/** Numeric-looking cells (digits, currency, %, separators) right-align. Values are never reformatted. */
const NUMERIC = /^[+\-−]?[$€£¥₹]?\s*\d[\d\s,._]*\s?[%‰]?$/;
/** A cell with no data (the backend writes a dash): never read as zero, never decides a column's type. */
const BLANK = "—";

function isNumericColumn(values: string[]): boolean {
  const sample = values.filter((v) => v.trim() !== "" && v.trim() !== BLANK).slice(0, 20);
  if (sample.length < 2) return false;
  return sample.every((v) => NUMERIC.test(v.trim()));
}

function toCsv(columns: string[], rows: Row[]): string {
  const escape = (value: unknown) => `"${String(value ?? "").replace(/"/g, '""')}"`;
  return [columns.map(escape).join(","), ...rows.map((row) => columns.map((c) => escape(row[c])).join(","))].join("\n");
}

/** Orders two non-blank cells: numerically when both read as numbers, otherwise as text. */
function compareCells(left: string, right: string): number {
  const numLeft = Number(left.replace(/[^0-9.\-+eE]/g, ""));
  const numRight = Number(right.replace(/[^0-9.\-+eE]/g, ""));
  if (Number.isFinite(numLeft) && Number.isFinite(numRight) && left.trim() !== "" && right.trim() !== "") return numLeft - numRight;
  return left.localeCompare(right);
}

/**
 * Sortable, selectable data table (shadcn Table + TanStack Table). Selection is by the row's position in `rows`,
 * so it survives re-sorting; pass `selectedIndex` to control it.
 */
export function DataTable({ columns, rows, initialSort, ariaLabel = "Analytical results table", caption, selectedIndex, onSelectedIndexChange }: {
  columns: string[];
  rows: Row[];
  /** The order the rows arrive in is the ranking the query asked for; show it as the active sort. */
  initialSort?: { column?: unknown; direction?: unknown };
  ariaLabel?: string;
  caption?: string;
  selectedIndex?: number | null;
  onSelectedIndexChange?: (index: number | null) => void;
}) {
  const numericCols = useMemo(() => {
    const map = new Map<string, boolean>();
    for (const col of columns) map.set(col, isNumericColumn(rows.map((row) => cellText(row[col]))));
    return map;
  }, [columns, rows]);

  const [sorting, setSorting] = useState<SortingState>(
    typeof initialSort?.column === "string" && columns.includes(initialSort.column)
      ? [{ id: initialSort.column, desc: initialSort.direction !== "asc" }]
      : [],
  );
  const [innerSelected, setInnerSelected] = useState<number | null>(null);
  const selected = selectedIndex === undefined ? innerSelected : selectedIndex;
  const select = (index: number | null) => (onSelectedIndexChange ?? setInnerSelected)(index);

  const columnDefs = useMemo<ColumnDef<Row>[]>(() => columns.map((key) => ({
    id: key,
    header: key,
    // A cell with no data is `undefined` so it sorts last in either direction.
    accessorFn: (row) => { const value = cellText(row[key]); return value === BLANK ? undefined : value; },
    cell: ({ row }) => cellText(row.original[key]),
    sortUndefined: "last",
    sortingFn: (a, b, id) => compareCells(String(a.getValue(id)), String(b.getValue(id))),
  })), [columns]);

  const table = useReactTable({
    data: rows,
    columns: columnDefs,
    state: { sorting },
    onSortingChange: setSorting,
    getRowId: (_, index) => String(index),
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });
  const tableRows = table.getRowModel().rows;
  const tall = tableRows.length > 20;
  const toggle = (index: number) => select(selected === index ? null : index);

  const download = () => {
    const blob = new Blob([toCsv(columns, tableRows.map((row) => row.original))], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "seleric_table.csv";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  return (
    <figure className="m-0 flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>{tableRows.length} row{tableRows.length === 1 ? "" : "s"}{tall && " · scroll for more"}</span>
        <Button type="button" variant="ghost" size="sm" onClick={download} aria-label="Download table as CSV">
          <DownloadIcon /> CSV
        </Button>
      </div>
      <div className="rounded-md border">
        <Table aria-label={ariaLabel} containerClassName={tall ? "max-h-96 overflow-y-auto" : undefined}>
          <TableHeader className={tall ? "sticky top-0 z-10 bg-card" : undefined}>
            {table.getHeaderGroups().map((group) => (
              <TableRow key={group.id}>
                {group.headers.map((header) => {
                  const sorted = header.column.getIsSorted();
                  return (
                    <TableHead
                      key={header.id}
                      scope="col"
                      className={numericCols.get(header.id) ? "num text-right" : undefined}
                      aria-sort={sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : "none"}
                    >
                      <Button type="button" variant="ghost" size="sm" className="-mx-2" onClick={header.column.getToggleSortingHandler()} title={`Sort by ${header.id}`}>
                        {String(header.column.columnDef.header)}
                        <span aria-hidden="true">{sorted === "asc" ? "▲" : sorted === "desc" ? "▼" : ""}</span>
                      </Button>
                    </TableHead>
                  );
                })}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {tableRows.map((row) => {
              const index = Number(row.id);
              return (
                <TableRow
                  key={row.id}
                  data-state={selected === index ? "selected" : undefined}
                  aria-selected={selected === index}
                  tabIndex={0}
                  onClick={() => toggle(index)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      toggle(index);
                    }
                  }}
                >
                  {row.getVisibleCells().map((cell) => (
                    <TableCell key={cell.id} className={numericCols.get(cell.column.id) ? "num text-right tabular-nums" : undefined}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </TableCell>
                  ))}
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
      {caption && <figcaption className="text-xs text-muted-foreground">{caption}</figcaption>}
    </figure>
  );
}
