"""Markdown table cells.

A value that holds "|" splits its row into extra columns unless the pipe is escaped: live 2026-10-08
(MS3-ed23dd03e2) the campaign "[Google Build] PMax - Seasonal New | 27th May" pushed every later column one to the
left, and the answer ranked a 21,776 "net ROAS" that was the campaign's ad spend.
"""

from __future__ import annotations

from typing import Any


def table_cell(value: Any) -> str:
    """``value`` as one Markdown table cell: pipes escaped (once), line breaks folded."""
    text = " ".join(str(value).splitlines())
    return text.replace("\\|", "|").replace("|", "\\|")
