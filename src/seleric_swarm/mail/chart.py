"""Email charts: a PNG line chart per time-series dataset, drawn from the stored rows (never from numbers the model typed).

One panel per metric, because metrics of different scales (orders vs net sales) do not share an axis.
shortcut: datasets split by a dimension (e.g. per campaign) get no chart, add a line per dimension value if asked for.
"""

from __future__ import annotations

from io import BytesIO

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter, PercentFormatter

from seleric_swarm.mail.excel import Dataset, _date

MAX_PANELS = 4
_MIN_POINTS = 2


def _series_columns(ds: Dataset) -> list:
    keys = {c.key for c in ds.columns}
    if "period_start" not in keys or "period_end" in keys:  # a window total, not a trend
        return []
    return [c for c in ds.columns if c.kind in ("number", "currency", "percent")]


def chartable(ds: Dataset) -> bool:
    dims = [c for c in ds.columns if c.key not in ("period_start", "period_end", "period") and c.kind == "text"]
    return not dims and len(ds.rows) >= _MIN_POINTS and bool(_series_columns(ds))


def chart_png(ds: Dataset) -> bytes | None:
    """PNG bytes of the dataset's trend, or None when it is not a time series."""
    if not chartable(ds):
        return None
    cols = _series_columns(ds)[:MAX_PANELS]
    rows = [(when, r) for r in ds.rows if (when := _date(r.get("period_start"))) is not None]
    if len(rows) < _MIN_POINTS:
        return None
    fig, axes = plt.subplots(len(cols), 1, figsize=(7.2, 1.9 * len(cols) + 0.7), sharex=True, squeeze=False)
    try:
        for ax, col in zip(axes[:, 0], cols, strict=True):
            pts = [(when, r[col.key]) for when, r in rows if isinstance(r.get(col.key), (int, float))]
            ax.plot([p[0] for p in pts], [p[1] for p in pts], marker="o", color="#2563eb", linewidth=2)
            ax.set_title(col.header, loc="left", fontsize=10)
            ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=1) if col.kind == "percent"
                                         else FuncFormatter(lambda v, _: f"{v:,.0f}"))
            ax.grid(axis="y", color="#e5e7eb")
            ax.spines[["top", "right"]].set_visible(False)
        axes[-1, 0].xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
        fig.suptitle(ds.name, x=0.01, ha="left", fontsize=11, fontweight="bold")
        fig.tight_layout()
        buf = BytesIO()
        fig.savefig(buf, format="png", dpi=110)
        return buf.getvalue()
    finally:
        plt.close(fig)
