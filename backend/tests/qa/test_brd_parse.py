from pathlib import Path

import pytest

from app.qa.brd.chunk import MAX_CHARS, chunk_document
from app.qa.brd.parse import (
    Block,
    BrdParseError,
    ParsedDoc,
    detect_format,
    numbered_heading,
    parse_bytes,
    parse_text,
)

SAMPLES = Path(__file__).resolve().parents[3] / "demo" / "brds"
FORMATS = ["juice-shop.md", "juice-shop.docx", "juice-shop.pdf"]


def load(name: str) -> ParsedDoc:
    return parse_bytes((SAMPLES / name).read_bytes(), name)


@pytest.mark.parametrize("name", FORMATS)
def test_all_formats_yield_headings_tables_and_requirement_text(name: str) -> None:
    doc = load(name)
    kinds = {b.kind for b in doc.blocks}
    assert {"heading", "text", "table"} <= kinds, (name, kinds)
    headings = [b.text for b in doc.blocks if b.kind == "heading"]
    assert any("3.1 Registration and Login" in h for h in headings), headings
    body = "\n".join(b.text for b in doc.blocks)
    # the same requirement sentences survive every format
    assert "register a customer account" in body
    assert "FR-9" in body and "BR-1" in body and "NFR-3" in body


@pytest.mark.parametrize("name", FORMATS)
def test_tables_keep_their_cells(name: str) -> None:
    tables = [b.text for b in load(name).blocks if b.kind == "table"]
    joined = "\n".join(tables)
    assert "Administrator" in joined and "Customer" in joined
    assert "Quantity" in joined and "Whole number between 1 and 5" in joined


def test_pdf_has_page_numbers_and_docx_does_not() -> None:
    pdf, docx = load("juice-shop.pdf"), load("juice-shop.docx")
    assert pdf.page_count == 3 and all(b.page for b in pdf.blocks)
    assert docx.page_count is None and all(b.page is None for b in docx.blocks)


@pytest.mark.parametrize("name", FORMATS)
def test_chunks_carry_section_paths_and_stay_small(name: str) -> None:
    chunks = chunk_document(load(name))
    assert chunks and [c.ordinal for c in chunks] == list(range(len(chunks)))
    assert all(len(c.text) <= MAX_CHARS + 400 for c in chunks)
    fr9 = next(c for c in chunks if "FR-9" in c.text)
    assert "3.3 Basket and Checkout" in fr9.section_path
    assert any(c.kind == "table" for c in chunks)


def test_pdf_chunks_know_their_page() -> None:
    chunks = chunk_document(load("juice-shop.pdf"))
    nfr = next(c for c in chunks if "NFR-3" in c.text)
    assert nfr.page_from is not None and nfr.page_from >= 2


def test_a_long_section_is_split_without_losing_text() -> None:
    sentence = "The system shall do something specific and testable number {}. "
    para = "".join(sentence.format(i) for i in range(120))
    chunks = chunk_document(
        ParsedDoc([Block("heading", "1 Big", 1), Block("text", para)])
    )
    assert len(chunks) > 1 and all(c.section_path == "1 Big" for c in chunks)
    assert "".join(c.text for c in chunks).replace(" ", "") == para.replace(" ", "")


def test_a_wide_table_is_split_by_rows_and_repeats_its_header() -> None:
    rows = "\n".join(
        f"| Field {i} | Rule number {i} is quite long " + "x" * 80 + " |"
        for i in range(60)
    )
    table = "| Field | Rule |\n|---|---|\n" + rows
    chunks = chunk_document(
        ParsedDoc([Block("heading", "Rules", 1), Block("table", table)])
    )
    assert len(chunks) > 1
    assert all(c.text.startswith("| Field | Rule |") for c in chunks)


def test_text_parser_handles_markdown_and_numbered_plain_text() -> None:
    md = b"# Title\n\n## 1. Scope\n\nSome text.\n\n- one\n- two\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"
    kinds = [(b.kind, b.level) for b in parse_text(md).blocks]
    assert kinds == [
        ("heading", 1),
        ("heading", 2),
        ("text", 0),
        ("list", 0),
        ("table", 0),
    ]
    plain = b"3.2 User Management\n\nFR-7: Users can log in.\n"
    blocks = parse_text(plain).blocks
    assert (
        blocks[0].kind == "heading"
        and blocks[0].level == 2
        and blocks[1].text.startswith("FR-7")
    )


@pytest.mark.parametrize(
    ("line", "result"),
    [
        ("3.2 User Management", 2),
        ("4. Validation Rules", 1),
        ("1.2.3 Deep Section", 3),
        ("FR-1: shall do x", None),
        ("2023 was a good year for sales and many things.", None),
        ("3 items were found.", None),
    ],
)
def test_numbered_heading(line: str, result: int | None) -> None:
    got = numbered_heading(line)
    assert (got[0] if got else None) == result


def test_unsupported_and_broken_inputs_are_rejected_with_clear_errors() -> None:
    with pytest.raises(BrdParseError, match="empty"):
        parse_bytes(b"", "x.pdf")
    with pytest.raises(BrdParseError, match="unsupported"):
        parse_bytes(bytes(range(256)) * 4, "x.bin")
    with pytest.raises(BrdParseError, match="PDF"):
        parse_bytes(b"%PDF-1.7 this is not really a pdf", "x.pdf")
    with pytest.raises(BrdParseError, match="DOCX"):
        parse_bytes(b"PK\x03\x04word/ truncated", "x.docx")
    with pytest.raises(BrdParseError, match="no readable content"):
        parse_bytes(b"# Only a heading\n", "x.md")
    with pytest.raises(BrdParseError, match="larger than"):
        parse_bytes(b"a" * (21 * 1024 * 1024), "x.txt")


def test_format_is_detected_from_bytes_not_extension() -> None:
    assert (
        detect_format((SAMPLES / "juice-shop.pdf").read_bytes(), "renamed.txt") == "pdf"
    )
    assert (
        detect_format((SAMPLES / "juice-shop.docx").read_bytes(), "renamed.pdf")
        == "docx"
    )


def test_every_requirement_line_survives_pdf_parsing() -> None:
    """Regression: a ruled one-row box was mistaken for a table and its paragraph was lost."""
    body = "\n".join(b.text for b in load("juice-shop.pdf").blocks)
    for n in range(1, 21):
        assert f"FR-{n}:" in body, f"FR-{n} was lost from the PDF"
    assert "custo | mer" not in body


@pytest.mark.parametrize("name", FORMATS)
def test_parsing_does_not_drop_content(name: str) -> None:
    doc = load(name)
    assert doc.warnings == []


def test_word_coverage_reports_loss() -> None:
    from app.qa.brd.parse import word_coverage

    assert word_coverage("alpha beta gamma delta", "alpha beta gamma delta") == 1.0
    assert word_coverage("alpha beta gamma delta", "alpha beta") == 0.5
    assert word_coverage("Page 1 alpha", "alpha", ignore={"Page 1"}) == 1.0


def _scanned_pdf() -> bytes:
    """A page whose text exists only as pixels."""
    import pymupdf

    src = pymupdf.open()
    page = src.new_page()
    page.insert_text((72, 90), "Business Requirements", fontsize=22)
    page.insert_text((72, 140), "1. Login", fontsize=16)
    page.insert_text(
        (72, 175),
        "FR-1: The user shall sign in with an email and a password.",
        fontsize=11,
    )
    page.insert_text(
        (72, 195), "FR-2: A locked account shall show an error message.", fontsize=11
    )
    out = pymupdf.open()
    scan = out.new_page(width=page.rect.width, height=page.rect.height)
    scan.insert_image(scan.rect, pixmap=page.get_pixmap(dpi=150))
    assert not scan.get_text().strip(), "the fixture must have no text layer"
    return out.tobytes()


def test_scanned_pdf_is_read_with_ocr() -> None:
    parsed = parse_bytes(_scanned_pdf(), "scan.pdf")
    text = " ".join(b.text for b in parsed.blocks).lower()
    assert "fr-1" in text and "sign in with an email" in text
    assert "locked account" in text
    assert any(b.kind == "heading" for b in parsed.blocks)
    assert any("OCR" in w for w in parsed.warnings), parsed.warnings


def test_pdf_ligatures_are_expanded() -> None:
    from app.qa.brd.parse import unligature

    assert unligature("notiﬁed ﬂow oﬃce") == "notified flow office"
