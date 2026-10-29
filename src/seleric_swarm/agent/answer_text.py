"""Plain-text clean-ups applied to the final answer (moved from ``agent/validation/answer_audit.py``)."""

from __future__ import annotations

from collections.abc import Callable


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


def numeric_tokens(text: str) -> set[str]:
    """Every number written in ``text`` as a normalised token (separators dropped, trailing decimal zeros trimmed)."""
    tokens: set[str] = set()
    current = ""
    padded = text + " "
    for i, ch in enumerate(padded[:-1]):
        nxt = padded[i + 1]
        if ch.isdigit():
            current += ch
        elif ch in ",." and current and nxt.isdigit():
            if ch == ".":
                current += ch
        else:
            if current:
                tokens.add(current.rstrip("0").rstrip(".") if "." in current else current)
            current = ""
    if current:
        tokens.add(current.rstrip("0").rstrip(".") if "." in current else current)
    return tokens


def strip_markdown_tables(text: str, covered: Callable[[list[str]], bool] | None = None) -> str:
    """Remove GFM Markdown tables from prose, leaving surrounding sentences.

    When a chart or table is attached the UI already shows the same rows, so a Markdown table in the answer rendered
    the data twice. A table's italic caption line belongs to the table and goes with it. ``covered`` (given a table's
    lines) says whether the attached visual holds that table's figures; a table it does not cover stays.
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
            start = i
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                i += 1
            if covered is not None and not covered(lines[start:i]):
                out.extend(lines[start:i])
                continue
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


def indian_digit_grouping(text: str) -> str:
    """Write every figure of six or more digits with Indian grouping (₹9,82,074, not ₹982,074), as the answer rules say.

    Only an amount is touched (comma-grouped already, or right after a currency sign) with six or more digits; a bare
    number (pincode 560001, order 1234567), a digit run glued to letters (SKU123456) and the decimal part stay as written."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if not text[i].isdigit() or (i > 0 and (text[i - 1].isalpha() or text[i - 1] in "_.")):
            out.append(text[i])
            i += 1
            continue
        j = i
        while j < n and (text[j].isdigit() or (text[j] == "," and j + 1 < n and text[j + 1].isdigit())):
            j += 1
        token = text[i:j]
        digits = token.replace(",", "")
        glued = j < n and (text[j].isalpha() or text[j] == "_")
        # an amount (already comma-grouped, or right after a currency sign) - never a bare id, pincode or order number
        amount = "," in token or (i > 0 and text[i - 1] in "₹$€£")
        if amount and len(digits) >= 6 and not glued and all(len(g) == 3 for g in token.split(",")[1:]):
            head, tail = digits[:-3], digits[-3:]
            groups: list[str] = []
            while len(head) > 2:
                groups.insert(0, head[-2:])
                head = head[:-2]
            if head:
                groups.insert(0, head)
            token = ",".join([*groups, tail])
        out.append(token)
        i = j
    return "".join(out)


def _bullet_body(line: str) -> str | None:
    """The text of a list item (bullet or "1." / "1)" numbered), without its marker; None for any other line."""
    text = line.strip()
    for marker in ("- ", "* ", "• ", "– "):
        if text.startswith(marker):
            return text[len(marker):]
    digits = 0
    while digits < len(text) and text[digits].isdigit():
        digits += 1
    if 0 < digits < len(text) - 1 and text[digits] in ".)" and text[digits + 1] == " ":
        return text[digits + 2:]
    return None


DENSE_ITEM_FIGURES = 6  # an item stating this many covered figures lists rows by itself


def drop_enumerations(text: str, covered_tokens: set[str], *, min_run: int = 4) -> str:
    """Remove a run of list items that only re-state figures an attached table or chart already shows.

    An item counts when it states at least one number and every number in it is one of ``covered_tokens`` (the
    attached visual's figures). Four or more such items in a row are a row-by-row listing of the data; one or two are
    findings and stay, as does any item that adds a figure the visual does not hold."""
    lines = text.splitlines()
    covered: list[bool] = []
    dense: list[bool] = []
    for line in lines:
        body = _bullet_body(line)
        tokens = numeric_tokens(body) if body is not None else set()
        covered.append(body is not None and bool(tokens) and tokens <= covered_tokens)
        dense.append(covered[-1] and len(numeric_tokens(body or "")) >= DENSE_ITEM_FIGURES)
    keep = [True] * len(lines)
    i = 0
    while i < len(lines):
        if not covered[i]:
            i += 1
            continue
        j = i
        while j < len(lines) and covered[j]:
            j += 1
        if j - i >= min_run or any(dense[i:j]):  # or an item that is itself a listing (a whole series on one line)
            for k in range(i, j):
                keep[k] = False
            lead = i - 1  # the line that introduced the listing ("Day by day:") goes with it
            while lead >= 0 and not lines[lead].strip():
                lead -= 1
            if lead >= 0 and _bullet_body(lines[lead]) is None and lines[lead].rstrip().endswith(":"):
                keep[lead] = False
        i = j
    out = [line for line, k in zip(lines, keep, strict=True) if k]
    collapsed: list[str] = []
    for line in out:
        if not line.strip() and collapsed and not collapsed[-1].strip():
            continue
        collapsed.append(line)
    return "\n".join(collapsed).strip() if len(out) != len(lines) else text


def with_roundings(tokens: set[str]) -> set[str]:
    """``tokens`` plus each decimal rounded to the whole number and to one place: a table's ₹14.60 is quoted as ₹15."""
    out = set(tokens)
    for token in tokens:
        if "." in token:
            try:
                value = float(token)
            except ValueError:
                continue
            out.add(str(round(value)))
            out.add(f"{value:.1f}".rstrip("0").rstrip("."))
    return out
