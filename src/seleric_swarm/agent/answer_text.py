"""Plain-text clean-ups applied to the final answer (moved from ``agent/validation/answer_audit.py``)."""

from __future__ import annotations


def _is_word_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def _sentence_start(text: str, index: int) -> bool:
    before = text[:index].rstrip(" *_`(\"'")
    return not before or before[-1] in ".!?:\n|"


def replace_metric_ids(text: str, labels: dict[str, str]) -> str:
    """Write each internal metric id in ``text`` as its catalogue display name.

    A leaked id is a wording slip with a known fix, so it is corrected in place rather than costing another model pass.
    Whole-word matches only (an id inside a longer id is left alone); ids without an underscore are left alone because
    they read as ordinary words ("orders", "sessions").
    """
    for metric_id, label in labels.items():
        token = metric_id.strip()
        name = (label or "").strip()
        if len(token) < 5 or "_" not in token or not name or name == token:
            continue
        out: list[str] = []
        pos = 0
        while (hit := text.find(token, pos)) >= 0:
            end = hit + len(token)
            whole = (hit == 0 or not _is_word_char(text[hit - 1])) and (end == len(text) or not _is_word_char(text[end]))
            out.append(text[pos:hit])
            if whole:
                # "Total sales" mid-sentence reads "total sales"; an acronym ("ROAS") stays.
                lower_ok = len(name) > 1 and name[1].islower() and not _sentence_start(text, hit)
                out.append(name[0].lower() + name[1:] if lower_ok else name)
            else:
                out.append(token)
            pos = end
        out.append(text[pos:])
        text = "".join(out)
    return text


def round_long_decimals(text: str, *, min_decimals: int = 5) -> str:
    """Round a raw float copied from a tool result (₹-41,920.83203758388) to a display value.

    Only numbers with ``min_decimals`` or more decimal digits are touched, so a ratio such as 0.838 or a price such as
    ₹73,136.85 stays as written. 100 and above round to whole numbers with thousands separators, 1 to 100 to 2 decimals, below 1 to 4 significant digits.
    """
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if not text[i].isdigit():
            out.append(text[i])
            i += 1
            continue
        if i > 0 and (text[i - 1].isalpha() or text[i - 1] == "_"):  # a code (SKU12345.678901): left as written
            j = i
            while j < n and (text[j].isalnum() or text[j] in "_."):
                j += 1
            out.append(text[i:j])
            i = j
            continue
        j = i
        while j < n and (text[j].isdigit() or (text[j] == "," and j + 1 < n and text[j + 1].isdigit())):
            j += 1
        k = j
        if j + 1 < n and text[j] == "." and text[j + 1].isdigit():
            k = j + 1
            while k < n and text[k].isdigit():
                k += 1
        decimals = k - j - 1 if k > j else 0
        token = text[i:k]
        if decimals >= min_decimals and not (k < n and (text[k].isalpha() or text[k] == "_")):
            value = float(token.replace(",", ""))
            if abs(value) >= 100:
                token = f"{round(value):,}"
            elif abs(value) >= 1:
                token = f"{value:.2f}"
            else:
                token = f"{value:.4g}" if value == 0 or abs(value) >= 1e-4 else f"{value:.6f}"  # a rate must not become 0.00
        out.append(token)
        i = k
    return "".join(out)


def is_table_delimiter(line: str) -> bool:
    """A GFM table delimiter row such as ``| --- | :--: |``."""
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    for cell in cells:
        dashes = cell.strip(":")
        if len(dashes) < 2 or dashes.strip("-"):
            return False
    return True


def _is_table_caption(line: str) -> bool:
    """A whole line in single underscores: the renderer's table caption."""
    text = line.strip()
    return len(text) > 2 and text.startswith("_") and text.endswith("_") and "__" not in text and "|" not in text


def strip_markdown_tables(text: str) -> str:
    """Remove GFM Markdown tables from prose, leaving surrounding sentences.

    When a chart is attached the UI already shows the same rows under the
    chart's Table tab, so a Markdown table in the answer rendered the data
    twice. A table's italic caption line belongs to the table and goes with it.
    """
    if not text:
        return text
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if "|" in line and "|" in nxt and is_table_delimiter(nxt):
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                i += 1
            gap = i + 1 if i < len(lines) and not lines[i].strip() else i
            if gap < len(lines) and _is_table_caption(lines[gap]):
                i = gap + 1
            if out and out[-1].strip() == "" and i < len(lines) and lines[i].strip() == "":
                i += 1
            continue
        out.append(line)
        i += 1
    collapsed: list[str] = []
    blank = False
    for line in out:
        if not line.strip():
            if blank:
                continue
            blank = True
            collapsed.append("")
            continue
        blank = False
        collapsed.append(line)
    return "\n".join(collapsed).strip()
