"""A layout-aware PDF converter for MarkItDown.

MarkItDown 0.1.7's built-in PDF path is tuned for invoices and forms. On a
document with several unrelated tables per page, or with more than one text
column, it produces three specific defects:

1. Words fuse together ("Newwayofdoingbusiness"). It splits words on a fixed
   3pt gap, but a heading set at 7pt with no space glyphs in the PDF has
   sub-point gaps between words, so nothing splits.
2. Tables grow phantom empty columns. It builds one column grid for the whole
   page and forces every table on that page into it, so a 3-column table
   sitting above a 4-column table gains a fourth, empty column.
3. Multi-column pages interleave. It groups words into rows by vertical
   position across the full page width, so the left and right columns of a
   two-column layout are read as single lines and the sentences run together.

This converter addresses all three: word gaps scale with font size, each table
keeps its own grid, and text columns are detected and read in order. It is
registered ahead of the built-in converter; if anything here raises, MarkItDown
falls through to the built-in converter on its own.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
from typing import Any, BinaryIO

from markitdown import DocumentConverter, DocumentConverterResult, StreamInfo

logger = logging.getLogger(__name__)

ACCEPTED_MIME_TYPE_PREFIXES = ["application/pdf", "application/x-pdf"]
ACCEPTED_FILE_EXTENSIONS = [".pdf"]

# Word gaps are measured relative to character size rather than in absolute
# points, so 7pt display type and 11pt body text both split correctly. 0.15
# splits the tightly kerned headings that broke the built-in converter without
# splitting inside words at normal tracking.
X_TOLERANCE_RATIO = 0.15

# A horizontal gap wider than this (in points) separates two cells of a row
# rather than two words of a sentence.
CELL_GAP_PT = 8.0

# An unwritten vertical band at least this wide, running the height of a block
# of text, is a gutter between columns rather than an indent.
MIN_GUTTER_PT = 14.0

# Cells hold values and short labels; anything longer is prose that merely
# happens to line up.
MAX_CELL_CHARS = 30

# A run of aligned lines becomes a table at this many rows and columns.
MIN_TABLE_ROWS = 2
MIN_TABLE_COLUMNS = 3

# Beyond this, "columns" are really a dense grid, and splitting the text into
# them would scramble it.
MAX_TEXT_COLUMNS = 3

# Fewer lines than this is too little evidence for a column layout.
MIN_COLUMN_LINES = 4

_WHITESPACE = re.compile(r"\s+")


@dataclass
class Line:
    """One visual line: its words already grouped into cells."""

    top: float
    cells: list[tuple[float, float, str]]  # (x0, x1, text), left to right

    @property
    def text(self) -> str:
        return " ".join(text for _, _, text in self.cells)

    @property
    def x0(self) -> float:
        return self.cells[0][0]

    @property
    def long_cells(self) -> int:
        return sum(1 for _, _, text in self.cells if len(text) > MAX_CELL_CHARS)

    @property
    def is_grid_like(self) -> bool:
        """Does this line look like a row of a table rather than a sentence?

        One wide cell is allowed — a row often carries a long name next to
        several short values — but two or more mean this is prose that merely
        happens to line up.
        """
        return len(self.cells) >= MIN_TABLE_COLUMNS and self.long_cells <= 1


def _clean(text: str) -> str:
    return _WHITESPACE.sub(" ", (text or "").replace("|", "\\|")).strip()


def _render_table(rows: list[list[str]]) -> str:
    """Render rows as a Markdown table, dropping columns that are entirely empty."""
    rows = [[_clean(cell) for cell in row] for row in rows]
    rows = [row for row in rows if any(row)]
    if not rows:
        return ""

    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]

    # A column no row ever fills is an artefact of the grid, not part of the
    # table. Dropping it is what keeps unrelated tables from bleeding together.
    keep = [i for i in range(width) if any(row[i] for row in rows)]
    if not keep:
        return ""
    rows = [[row[i] for i in keep] for row in rows]

    widths = [max(len(row[i]) for row in rows) for i in range(len(keep))]
    widths = [max(w, 3) for w in widths]

    def line(cells: list[str]) -> str:
        return "| " + " | ".join(c.ljust(w) for c, w in zip(cells, widths)) + " |"

    header, *body = rows
    out = [line(header), "| " + " | ".join("-" * w for w in widths) + " |"]
    out.extend(line(row) for row in body)
    return "\n".join(out)


def _group_lines(words: list[dict]) -> list[Line]:
    """Group words into visual lines, then into cells within each line."""
    if not words:
        return []

    ordered = sorted(words, key=lambda w: (w["top"], w["x0"]))
    buckets: list[list[dict]] = [[ordered[0]]]
    for word in ordered[1:]:
        current = buckets[-1]
        reference = current[0]
        height = max(reference["bottom"] - reference["top"], 1.0)
        # Same line if the baselines are within half a character height.
        if abs(word["top"] - reference["top"]) <= max(2.0, height * 0.5):
            current.append(word)
        else:
            buckets.append([word])

    lines: list[Line] = []
    for bucket in buckets:
        bucket.sort(key=lambda w: w["x0"])
        cells: list[list[Any]] = [[bucket[0]["x0"], bucket[0]["x1"], [bucket[0]["text"]]]]
        previous = bucket[0]
        for word in bucket[1:]:
            if word["x0"] - previous["x1"] > CELL_GAP_PT:
                cells.append([word["x0"], word["x1"], [word["text"]]])
            else:
                cells[-1][1] = word["x1"]
                cells[-1][2].append(word["text"])
            previous = word
        merged = [(x0, x1, _clean(" ".join(parts))) for x0, x1, parts in cells]
        merged = [(x0, x1, text) for x0, x1, text in merged if text]
        if merged:
            lines.append(Line(top=bucket[0]["top"], cells=merged))
    return lines


def _column_grid(lines: list[Line]) -> list[float]:
    """Derive shared column positions from a run of table rows.

    The grid is built only from the rows of this one table, which is what
    stops a neighbouring table's columns from leaking in.
    """
    positions = sorted(x0 for line in lines for x0, _, _ in line.cells)
    if not positions:
        return []
    tolerance = max(CELL_GAP_PT, MIN_GUTTER_PT)
    grid = [positions[0]]
    for x0 in positions[1:]:
        if x0 - grid[-1] > tolerance:
            grid.append(x0)
    return grid


def _rows_from_lines(lines: list[Line]) -> list[list[str]]:
    grid = _column_grid(lines)
    if len(grid) < MIN_TABLE_COLUMNS:
        return []

    rows: list[list[str]] = []
    for line in lines:
        row = [""] * len(grid)
        for x0, _, text in line.cells:
            index = min(
                range(len(grid)), key=lambda i: abs(grid[i] - x0)
            )
            row[index] = f"{row[index]} {text}".strip() if row[index] else text
        rows.append(row)
    return rows


def _find_gutters(lines: list[Line], page_width: float) -> list[float]:
    """Find vertical whitespace that runs through every line of a text block."""
    if len(lines) < 2:
        return []

    width = int(page_width) + 1
    occupied = bytearray(width)
    for line in lines:
        for x0, x1, _ in line.cells:
            start = max(0, int(x0))
            end = min(width - 1, int(x1))
            for x in range(start, end + 1):
                occupied[x] = 1

    left = min(int(line.x0) for line in lines)
    right = max(min(width - 1, int(x1)) for line in lines for _, x1, _ in line.cells)

    gutters: list[float] = []
    run_start: int | None = None
    for x in range(left, right + 1):
        if not occupied[x]:
            if run_start is None:
                run_start = x
        else:
            if run_start is not None and x - run_start >= MIN_GUTTER_PT:
                gutters.append((run_start + x) / 2.0)
            run_start = None
    return gutters


def _looks_like_wrapped_prose(lines: list[Line]) -> bool:
    """Guard against splitting a label/value block into columns.

    "Name: John" over "Date: 2024" has a clean vertical gap down the middle,
    but splitting there would file every label away from its value. Real text
    columns are made of wrapped sentences, so require some.
    """
    return len(lines) >= MIN_COLUMN_LINES and sum(
        1 for line in lines if line.long_cells
    ) >= 2


def _split_into_columns(lines: list[Line], page_width: float) -> list[list[Line]]:
    """Split a block of prose into text columns, read left to right."""
    if not _looks_like_wrapped_prose(lines):
        return [lines]

    gutters = _find_gutters(lines, page_width)
    if not gutters or len(gutters) + 1 > MAX_TEXT_COLUMNS:
        return [lines]

    columns: list[list[Line]] = [[] for _ in range(len(gutters) + 1)]
    for line in lines:
        for x0, x1, text in line.cells:
            index = sum(1 for gutter in gutters if x0 > gutter)
            column = columns[index]
            if column and column[-1].top == line.top:
                column[-1].cells.append((x0, x1, text))
            else:
                column.append(Line(top=line.top, cells=[(x0, x1, text)]))
    columns = [column for column in columns if column]
    # A "column" holding a single stray line is an indent, not a column.
    if any(len(column) < 2 for column in columns):
        return [lines]
    return columns


def _paragraphs(lines: list[Line]) -> str:
    """Join lines into text, restoring the blank line between paragraphs.

    A paragraph break in a PDF is just extra leading, so compare each gap
    against the block's usual line pitch.
    """
    if not lines:
        return ""
    gaps = sorted(b.top - a.top for a, b in zip(lines, lines[1:]) if b.top > a.top)
    # The ordinary line pitch is the *small* end of the distribution. A median
    # would be dragged up by the paragraph gaps this is meant to detect.
    pitch = gaps[len(gaps) // 4] if gaps else 0.0

    out = [lines[0].text]
    for previous, line in zip(lines, lines[1:]):
        if pitch and (line.top - previous.top) > pitch * 1.4:
            out.append("")
        out.append(line.text)
    return "\n".join(out)


def _render_block(lines: list[Line], page_width: float) -> list[str]:
    """Turn a block of lines into Markdown, separating tables from prose."""
    chunks: list[str] = []
    index = 0
    while index < len(lines):
        if lines[index].is_grid_like:
            end = index
            while end < len(lines) and lines[end].is_grid_like:
                end += 1
            run = lines[index:end]
            if len(run) >= MIN_TABLE_ROWS:
                table = _render_table(_rows_from_lines(run))
                if table:
                    chunks.append(table)
                    index = end
                    continue
            # Not enough rows to be a table; fall through and treat as prose.
            for line in run:
                chunks.append(line.text)
            index = end
            continue

        end = index
        while end < len(lines) and not lines[end].is_grid_like:
            end += 1
        prose = lines[index:end]
        for column in _split_into_columns(prose, page_width):
            column.sort(key=lambda line: line.top)
            for line in column:
                line.cells.sort(key=lambda cell: cell[0])
            chunks.append(_paragraphs(column))
        index = end

    return [chunk for chunk in chunks if chunk.strip()]


def _convert_page(page: Any) -> str:
    """Convert one page, keeping ruled tables and text in reading order."""
    ruled = []
    try:
        found = page.find_tables(
            {"vertical_strategy": "lines", "horizontal_strategy": "lines"}
        )
        for table in found:
            rendered = _render_table(table.extract())
            if rendered:
                ruled.append((table.bbox, rendered))
    except Exception:  # noqa: BLE001 - a bad table must not lose the page
        logger.debug("Ruled table detection failed on a page", exc_info=True)
    ruled.sort(key=lambda item: item[0][1])

    words = page.extract_words(
        x_tolerance_ratio=X_TOLERANCE_RATIO,
        y_tolerance=3,
        keep_blank_chars=False,
        use_text_flow=False,
    )

    # Walk the page top to bottom: text above the first ruled table, that
    # table, text down to the next one, and so on.
    chunks: list[str] = []
    cursor = 0.0
    for bbox, rendered in ruled + [((0.0, page.height, 0.0, page.height), None)]:
        left, top, right, bottom = bbox
        above = [w for w in words if w["top"] >= cursor and w["bottom"] <= top]
        chunks.extend(_render_block(_group_lines(above), page.width))
        if rendered:
            chunks.append(rendered)
            # A table rarely spans the full width. Whatever sits beside it is
            # ordinary text, and dropping it would lose content outright, so
            # emit it after the table rather than not at all.
            beside = [
                w
                for w in words
                if w["top"] >= top
                and w["bottom"] <= bottom
                and (w["x1"] < left or w["x0"] > right)
            ]
            chunks.extend(_render_block(_group_lines(beside), page.width))
        cursor = max(cursor, bottom)

    if not chunks:
        text = page.extract_text(x_tolerance_ratio=X_TOLERANCE_RATIO) or ""
        return text.strip()
    return "\n\n".join(chunks)


class LayoutAwarePdfConverter(DocumentConverter):
    """Converts PDFs to Markdown, preserving tables and column reading order."""

    def accepts(
        self, file_stream: BinaryIO, stream_info: StreamInfo, **kwargs: Any
    ) -> bool:
        extension = (stream_info.extension or "").lower()
        if extension in ACCEPTED_FILE_EXTENSIONS:
            return True
        mimetype = (stream_info.mimetype or "").lower()
        return any(mimetype.startswith(p) for p in ACCEPTED_MIME_TYPE_PREFIXES)

    def convert(
        self, file_stream: BinaryIO, stream_info: StreamInfo, **kwargs: Any
    ) -> DocumentConverterResult:
        import pdfplumber  # imported here so a missing dependency defers to markitdown

        pdf_bytes = io.BytesIO(file_stream.read())
        pages: list[str] = []
        with pdfplumber.open(pdf_bytes) as pdf:
            for page in pdf.pages:
                try:
                    text = _convert_page(page)
                except Exception:  # noqa: BLE001 - keep the rest of the document
                    logger.warning("Falling back to plain text for a page", exc_info=True)
                    text = (page.extract_text() or "").strip()
                if text:
                    pages.append(text)
                page.close()  # keep memory flat across a long document

        markdown = "\n\n".join(pages).strip()
        if not markdown:
            # Nothing extractable — let the built-in converter have its turn.
            raise ValueError("No text could be extracted from this PDF")
        return DocumentConverterResult(markdown=markdown)
