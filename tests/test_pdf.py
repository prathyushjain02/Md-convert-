"""Tests for the layout-aware PDF converter.

Fixtures are generated with reportlab so the cases are reproducible: each PDF
reproduces a specific defect seen when converting a real fund factsheet.
"""

from __future__ import annotations

import io
import re

import pytest

from app import create_app
from app.converter import convert_upload, use_layout_aware_pdf

reportlab_canvas = pytest.importorskip(
    "reportlab.pdfgen.canvas", reason="reportlab is needed to build PDF fixtures"
)
from reportlab.lib.pagesizes import A4  # noqa: E402

W, H = A4


def to_markdown(pdf_bytes: bytes, filename: str = "fixture.pdf") -> str:
    return convert_upload(io.BytesIO(pdf_bytes), filename=filename).markdown


def build(draw) -> bytes:
    buffer = io.BytesIO()
    canvas = reportlab_canvas.Canvas(buffer, pagesize=A4)
    draw(canvas)
    canvas.save()
    return buffer.getvalue()


def kerned(canvas, x, y, words, size=7, gap=1.4):
    """Draw words positioned individually, with no space characters at all.

    This is how display type is set in a designed document, and it is what
    made the built-in converter emit "Newwayofdoingbusiness".
    """
    canvas.setFont("Helvetica-Bold", size)
    for word in words:
        canvas.drawString(x, y, word)
        x += canvas.stringWidth(word, "Helvetica-Bold", size) + gap


def wrapped(canvas, x, y, text, width, size=9, leading=12):
    canvas.setFont("Helvetica", size)
    line = ""
    for word in text.split():
        trial = (line + " " + word).strip()
        if canvas.stringWidth(trial, "Helvetica", size) > width:
            canvas.drawString(x, y, line)
            y -= leading
            line = word
        else:
            line = trial
    if line:
        canvas.drawString(x, y, line)


LEFT_COLUMN = (
    "The fund follows a value investing discipline and holds a diversified "
    "portfolio of equities selected on the basis of long term earnings potential."
)
RIGHT_COLUMN = (
    "Investors should note that the scheme does not guarantee any returns. Past "
    "performance is not indicative of future results and units may go down."
)


@pytest.fixture(scope="module")
def two_column_pdf() -> bytes:
    """Two prose columns and kerned headings, on a page that also has a table.

    The table matters. Without one, MarkItDown routes the page to pdfminer and
    the prose comes out fine; it is the presence of table-like rows that sends
    the whole page down the form-extraction path that mangles the prose. A
    real factsheet page always has both.
    """

    def draw(canvas):
        kerned(canvas, 40, H - 50, ["New", "way", "of", "doing", "business"])
        kerned(canvas, 300, H - 50, ["Consumer", "preferences"])
        wrapped(canvas, 40, H - 100, LEFT_COLUMN, 220)
        wrapped(canvas, 300, H - 100, RIGHT_COLUMN, 220)

        rows = [
            ["Holding period", "Resident", "NRI", "Corporate"],
            ["Under 12 months", "20.0%", "20.0%", "25.0%"],
            ["Over 12 months", "12.5%", "12.5%", "12.5%"],
        ]
        for r, row in enumerate(rows):
            canvas.setFont("Helvetica", 8)
            for value, x in zip(row, [40, 170, 250, 330]):
                canvas.drawString(x, H - 300 - r * 14, value)

    return build(draw)


@pytest.fixture(scope="module")
def two_table_pdf() -> bytes:
    """One ruled 3-column table above a borderless 4-column table."""

    def draw(canvas):
        rows = [
            ["Security", "Weight", "Sector"],
            ["Infosys", "6.2%", "Technology"],
            ["HDFC Bank", "5.8%", "Financials"],
        ]
        y0, widths, height = H - 80, [110, 55, 90], 16
        for r, row in enumerate(rows):
            canvas.setFont("Helvetica", 8)
            x = 40
            for value, width in zip(row, widths):
                canvas.rect(x, y0 - r * height - height, width, height)
                canvas.drawString(x + 3, y0 - r * height - height + 5, value)
                x += width

        rows_b = [
            ["Holding period", "Resident", "NRI", "Corporate"],
            ["Under 12 months", "20.0%", "20.0%", "25.0%"],
            ["Over 12 months", "12.5%", "12.5%", "12.5%"],
        ]
        for r, row in enumerate(rows_b):
            canvas.setFont("Helvetica", 8)
            for value, x in zip(row, [40, 170, 250, 330]):
                canvas.drawString(x, H - 240 - r * 14, value)

    return build(draw)


def table_blocks(markdown: str) -> list[list[str]]:
    """Split Markdown into the sequences of lines that form tables."""
    blocks, current = [], []
    for line in markdown.splitlines():
        if line.startswith("|"):
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def column_count(row: str) -> int:
    return len([cell for cell in row.strip().strip("|").split("|")])


# --- word spacing ---------------------------------------------------------


def test_kerned_headings_keep_their_word_breaks(two_column_pdf):
    markdown = to_markdown(two_column_pdf)
    assert "New way of doing business" in markdown
    assert "Consumer preferences" in markdown
    assert "Newwayofdoingbusiness" not in markdown
    assert "Consumerpreferences" not in markdown


# --- reading order --------------------------------------------------------


def test_text_columns_are_not_interleaved(two_column_pdf):
    markdown = to_markdown(two_column_pdf)
    # No single line may carry text from both columns.
    for line in markdown.splitlines():
        assert not ("fund follows" in line and "Investors should" in line)


def test_text_columns_are_read_left_then_right(two_column_pdf):
    markdown = to_markdown(two_column_pdf)
    assert markdown.index("value investing") < markdown.index("Investors should note")
    # The left column runs to its end before the right column starts.
    assert markdown.index("earnings potential") < markdown.index("Investors should note")


# --- tables ---------------------------------------------------------------


def test_each_table_keeps_its_own_column_count(two_table_pdf):
    blocks = table_blocks(to_markdown(two_table_pdf))
    assert len(blocks) == 2, "the two tables must not be merged into one grid"

    widths = {len(block): column_count(block[0]) for block in blocks}
    counts = sorted(column_count(block[0]) for block in blocks)
    assert counts == [3, 4], f"expected a 3-column and a 4-column table, got {widths}"


def test_tables_have_no_phantom_empty_columns(two_table_pdf):
    for block in table_blocks(to_markdown(two_table_pdf)):
        for row in block:
            if set(row) <= set("|- "):
                continue  # separator row
            cells = row.strip().strip("|").split("|")
            assert cells[-1].strip(), f"trailing empty column in: {row}"


def test_table_rows_are_intact(two_table_pdf):
    markdown = to_markdown(two_table_pdf)
    assert re.search(r"\|\s*Infosys\s*\|\s*6\.2%\s*\|\s*Technology\s*\|", markdown)
    assert re.search(r"\|\s*Over 12 months\s*\|\s*12\.5%\s*\|", markdown)


def test_ruled_table_cells_keep_their_word_breaks():
    """The size-relative word gap has to reach inside table cells too.

    pdfplumber extracts cell text with its own defaults, so a table whose
    cells were set without space glyphs fused exactly like the headings did.
    """

    def draw(canvas):
        rows = [
            ["Housing Development Finance", "Financials"],
            ["Tata Consultancy Services", "Technology"],
        ]
        for r, row in enumerate(rows):
            x = 40
            for value, width in zip(row, [200, 120]):
                canvas.rect(x, 700 - r * 20, width, 20)
                cursor = x + 3
                for word in value.split():
                    canvas.setFont("Helvetica", 7)
                    canvas.drawString(cursor, 705 - r * 20, word)
                    cursor += canvas.stringWidth(word, "Helvetica", 7) + 1.4
                x += width

    markdown = to_markdown(build(draw))
    assert "Housing Development Finance" in markdown
    assert "Tata Consultancy Services" in markdown
    assert "HousingDevelopmentFinance" not in markdown


def test_a_long_cell_does_not_break_the_table():
    def draw(canvas):
        rows = [
            ["Company name", "Weight", "Sector"],
            ["Housing Development Finance Corporation Ltd", "7.4%", "Financials"],
            ["Tata Consultancy Services Limited", "5.1%", "Technology"],
        ]
        for r, row in enumerate(rows):
            canvas.setFont("Helvetica", 8)
            for value, x in zip(row, [40, 300, 360]):
                canvas.drawString(x, H - 70 - r * 14, value)

    markdown = to_markdown(build(draw))
    blocks = table_blocks(markdown)
    assert len(blocks) == 1
    assert "Housing Development Finance Corporation Ltd" in markdown
    assert column_count(blocks[0][0]) == 3


# --- layouts that must not be "fixed" -------------------------------------


def test_label_value_pairs_stay_on_one_line():
    """A clean vertical gap between labels and values is not a column break."""

    def draw(canvas):
        canvas.setFont("Helvetica", 10)
        pairs = [
            ("Fund Manager", "George Thomas"),
            ("Inception Date", "13 March 2006"),
            ("Benchmark", "BSE 500 TRI"),
            ("Exit Load", "1.00%"),
        ]
        for i, (label, value) in enumerate(pairs):
            canvas.drawString(60, H - 80 - i * 16, label)
            canvas.drawString(200, H - 80 - i * 16, value)

    markdown = to_markdown(build(draw))
    assert "Fund Manager George Thomas" in markdown
    assert "Inception Date 13 March 2006" in markdown


def test_single_column_prose_keeps_paragraph_breaks():
    def draw(canvas):
        canvas.setFont("Helvetica", 10)
        y = H - 90
        for line in [
            "The investment objective of the scheme is to achieve long term",
            "capital appreciation by investing primarily in equity securities.",
            "",
            "There is no assurance that the investment objective of the scheme",
            "will be realised, and the scheme does not guarantee any returns.",
        ]:
            if line:
                canvas.drawString(60, y, line)
            y -= 14

    markdown = to_markdown(build(draw))
    assert "\n\nThere is no assurance" in markdown
    # And the paragraph itself is not broken up.
    assert "The investment objective of the scheme is to achieve long term" in markdown


def test_plain_pdf_is_unchanged():
    def draw(canvas):
        canvas.setFont("Helvetica-Bold", 18)
        canvas.drawString(72, 720, "Quarterly Report")
        canvas.setFont("Helvetica", 12)
        canvas.drawString(72, 690, "Revenue increased by 12% year over year.")

    markdown = to_markdown(build(draw))
    assert "Quarterly Report" in markdown
    assert "Revenue increased by 12% year over year." in markdown


# --- engine selection and integration -------------------------------------


def test_engine_can_be_switched_back_to_markitdown(monkeypatch, two_column_pdf):
    assert use_layout_aware_pdf() is True
    monkeypatch.setenv("PDF_ENGINE", "markitdown")
    assert use_layout_aware_pdf() is False
    # The built-in converter still produces text; it just produces it worse.
    assert "fund follows" in to_markdown(two_column_pdf).replace("  ", " ")
    monkeypatch.undo()
    assert use_layout_aware_pdf() is True


def test_pdf_converts_through_the_api(two_column_pdf):
    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as client:
        response = client.post(
            "/api/convert/file",
            data={"files": (io.BytesIO(two_column_pdf), "factsheet.pdf")},
            content_type="multipart/form-data",
        )
    assert response.status_code == 200
    assert "factsheet.md" in response.headers["Content-Disposition"]
    assert b"New way of doing business" in response.data
