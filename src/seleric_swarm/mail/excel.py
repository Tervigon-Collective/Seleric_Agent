"""The email's Excel attachment, built from the datasets the user picked (never every query result).

Cell values come from the dataset rows as given: this module formats, it never recomputes. Every string goes in as a
string (``write_string``), so text like ``=HYPERLINK(...)`` is shown, not run. A dataset with fewer rows than
``total_rows`` is marked PARTIAL on the About sheet.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, Literal

import xlsxwriter

Kind = Literal["text", "number", "currency", "percent", "date"]
_FORMATS = {"currency": "#,##0.00", "percent": "0.0%", "number": "General", "date": "yyyy-mm-dd"}
_MAX_WIDTH = 60


@dataclass(frozen=True)
class Column:
    key: str
    header: str
    kind: Kind = "text"  # percent values are fractions (0.031 shows as 3.1%)
    unit: str = ""  # a currency code for kind "currency" (the email table writes it as ₹)


@dataclass(frozen=True)
class Dataset:
    name: str
    columns: list[Column]
    rows: list[dict[str, Any]]
    period: str = ""
    timezone: str = ""
    filters: str = ""
    source: str = ""
    definitions: dict[str, str] = field(default_factory=dict)  # column key -> metric definition
    total_rows: int | None = None  # rows the source holds, when more than ``rows``

    @property
    def partial(self) -> bool:
        return self.total_rows is not None and self.total_rows > len(self.rows)


def _sheet_names(datasets: list[Dataset]) -> list[str]:
    taken, names = {"about"}, []
    for ds in datasets:
        base = re.sub(r"[\[\]:*?/\\]", " ", ds.name).strip()[:31] or "Data"
        name, n = base, 1
        while name.lower() in taken:
            n += 1
            name = f"{base[: 31 - len(str(n)) - 1]} {n}"
        taken.add(name.lower())
        names.append(name)
    return names


def _date(value: Any) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time())
    try:
        return dt.datetime.combine(dt.date.fromisoformat(str(value)[:10]), dt.time())
    except ValueError:
        return None


def build_workbook(datasets: list[Dataset]) -> bytes:
    buf = BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True, "strings_to_formulas": False, "nan_inf_to_errors": False})
    about = wb.add_worksheet("About")
    head = wb.add_format({"bold": True, "bg_color": "#EEEEEE", "border": 1})
    fmts = {k: wb.add_format({"num_format": f}) for k, f in _FORMATS.items()}
    about_rows: list[tuple[str, str]] = []
    for ds, name in zip(datasets, _sheet_names(datasets), strict=True):
        ws = wb.add_worksheet(name)
        widths = [len(c.header) for c in ds.columns]
        for i, col in enumerate(ds.columns):
            ws.write_string(0, i, col.header, head)
        for r, row in enumerate(ds.rows, start=1):
            for i, col in enumerate(ds.columns):
                value = row.get(col.key)
                widths[i] = max(widths[i], len(str(value)) if value is not None else 0)
                if value is None or (isinstance(value, float) and not math.isfinite(value)):
                    continue
                if col.kind == "date" and (when := _date(value)) is not None:
                    ws.write_datetime(r, i, when, fmts["date"])
                elif col.kind != "text" and isinstance(value, (int, float)) and not isinstance(value, bool):
                    ws.write_number(r, i, value, fmts[col.kind])
                else:
                    ws.write_string(r, i, str(value))
        for i, w in enumerate(widths):
            ws.set_column(i, i, min(w + 2, _MAX_WIDTH))
        ws.freeze_panes(1, 0)
        ws.autofilter(0, 0, len(ds.rows), max(len(ds.columns) - 1, 0))
        status = (f"PARTIAL — {len(ds.rows)} of {ds.total_rows} rows" if ds.partial
                  else f"Complete — {len(ds.rows)} rows")
        about_rows += [("Sheet", name), ("Status", status), ("Period", ds.period), ("Timezone", ds.timezone),
                       ("Filters", ds.filters), ("Source", ds.source),
                       *((f"Definition: {k}", v) for k, v in ds.definitions.items()), ("", "")]
    about.write_string(0, 0, "Seleric session export", head)
    for r, (k, v) in enumerate(about_rows, start=2):
        about.write_string(r, 0, k)
        about.write_string(r, 1, v)
    about.set_column(0, 0, 28)
    about.set_column(1, 1, 70)
    wb.close()
    return buf.getvalue()
