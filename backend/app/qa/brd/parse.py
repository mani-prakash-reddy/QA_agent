"""BRD parsing: PDF, DOCX and Markdown/plain text -> an ordered list of structural blocks.

Structure matters more than raw text: headings give each requirement its section, tables carry
roles/validation rules, and page numbers let a reviewer find the source."""

import re
from collections import Counter
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, Literal

BlockKind = Literal["heading", "text", "list", "table"]

BULLET_RE = re.compile(r"^\s*(?:[•◦▪●■\-–*]|\(?[a-z]\)|\(?\d{1,2}\)|\d{1,2}[.)])\s+\S")
NUMBERED_HEADING_RE = re.compile(
    r"^\s*(\d{1,2}(?:\.\d{1,2}){0,4})[.)]?\s+([A-Z][^.!?]{1,100})$"
)
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MIN_COVERAGE = 0.97  # share of the page's words that must appear in the parsed blocks


class BrdParseError(Exception):
    pass


@dataclass
class Block:
    kind: BlockKind
    text: str
    level: int = 0  # headings only: 1 = top
    page: int | None = None


@dataclass
class ParsedDoc:
    blocks: list[Block] = field(default_factory=list)
    page_count: int | None = None
    format: str = "text"
    warnings: list[str] = field(default_factory=list)


def detect_format(data: bytes, filename: str) -> str:
    """Trust the bytes, not the extension."""
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:2] == b"PK":
        if (
            filename.lower().endswith(".docx")
            or b"word/" in data[:4000]
            or b"word/document.xml" in data
        ):
            return "docx"
        raise BrdParseError("unsupported ZIP-based file (only .docx is supported)")
    if filename.lower().endswith((".md", ".markdown", ".txt")):
        return "text"
    try:
        data[:4000].decode("utf-8")
        return "text"
    except UnicodeDecodeError as exc:
        raise BrdParseError(
            "unsupported file type (use PDF, DOCX, Markdown or text)"
        ) from exc


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9]{2,}", text.lower())


def word_coverage(raw: str, parsed: str, ignore: set[str] | None = None) -> float:
    """Fraction of the source words that survived parsing (repeated headers/footers excluded)."""
    skip = {w for line in (ignore or set()) for w in _words(line)}
    source = Counter(w for w in _words(raw) if w not in skip)
    if not source:
        return 1.0
    kept = Counter(_words(parsed))
    found = sum(min(n, kept[w]) for w, n in source.items())
    return found / sum(source.values())


def numbered_heading(line: str) -> tuple[int, str] | None:
    """'3.2 User Management' -> (2, line). Short, capitalised, numbered lines are headings."""
    m = NUMBERED_HEADING_RE.match(line.strip())
    if not m or len(line) > 110:
        return None
    return m.group(1).count(".") + 1, line.strip()


def _md_table(rows: list[list[str]]) -> str:
    rows = [
        [unligature(re.sub(r"\s+", " ", c)).strip().replace("|", "/") for c in r]
        for r in rows
        if any(c.strip() for c in r)
    ]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |", "|" + "|".join(["---"] * width) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(out)


# ---------------------------------------------------------------- plain text / markdown
def parse_text(data: bytes) -> ParsedDoc:
    text = data.decode("utf-8", errors="replace").replace("\r\n", "\n")
    blocks: list[Block] = []
    para: list[str] = []
    table: list[str] = []

    def flush_para() -> None:
        if para:
            joined = " ".join(s.strip() for s in para).strip()
            if joined:
                blocks.append(Block("text", joined))
            para.clear()

    def flush_table() -> None:
        if table:
            blocks.append(Block("table", "\n".join(table)))
            table.clear()

    items: list[str] = []

    def flush_list() -> None:
        if items:
            blocks.append(Block("list", "\n".join(items)))
            items.clear()

    for raw in text.split("\n"):
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            flush_para()
            flush_list()
            if not re.fullmatch(
                r"\|[\s:|-]+\|", stripped
            ):  # skip the --- separator row
                table.append(stripped)
            continue
        flush_table()
        if not stripped:
            flush_para()
            flush_list()
            continue
        md = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if md:
            flush_para()
            flush_list()
            blocks.append(Block("heading", md.group(2).strip(), level=len(md.group(1))))
            continue
        nh = numbered_heading(stripped)
        if nh and not para and not items:
            blocks.append(Block("heading", nh[1], level=nh[0]))
            continue
        if BULLET_RE.match(line):
            flush_para()
            items.append(re.sub(r"^\s*(?:[•◦▪●■\-–*])\s+", "- ", stripped))
            continue
        if items and raw.startswith((" ", "\t")):  # wrapped continuation of a list item
            items[-1] += " " + stripped
            continue
        flush_list()
        para.append(stripped)
    flush_para()
    flush_list()
    flush_table()
    return ParsedDoc(blocks=blocks, page_count=None, format="text")


# ---------------------------------------------------------------- DOCX
def parse_docx(data: bytes) -> ParsedDoc:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        doc = Document(BytesIO(data))
    except Exception as exc:
        raise BrdParseError(
            f"could not read the DOCX file: {type(exc).__name__}"
        ) from exc
    blocks: list[Block] = []
    list_items: list[str] = []

    def flush_list() -> None:
        if list_items:
            blocks.append(Block("list", "\n".join(list_items)))
            list_items.clear()

    for child in doc.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "tbl":
            flush_list()
            table = Table(child, doc)
            md = _md_table([[c.text for c in row.cells] for row in table.rows])
            if md:
                blocks.append(Block("table", md))
        elif tag == "p":
            para = Paragraph(child, doc)
            text = para.text.strip()
            if not text:
                flush_list()
                continue
            style = (para.style.name if para.style is not None else "") or ""
            m = re.match(r"Heading\s*(\d)", style, re.I)
            if m or style.lower() == "title":
                flush_list()
                blocks.append(Block("heading", text, level=int(m.group(1)) if m else 1))
            elif (
                "list" in style.lower()
                or BULLET_RE.match(text)
                or para._p.pPr is not None
                and para._p.pPr.numPr is not None
            ):
                list_items.append(
                    "- " + re.sub(r"^\s*(?:[•◦▪●■\-–*]|\d{1,2}[.)])\s+", "", text)
                )
            elif (
                (nh := numbered_heading(text))
                and len(text) < 100
                and not text.endswith((".", ";", ","))
            ):
                flush_list()
                blocks.append(Block("heading", nh[1], level=nh[0]))
            else:
                flush_list()
                blocks.append(Block("text", text))
    flush_list()
    parsed_docx = ParsedDoc(blocks=blocks, page_count=None, format="docx")
    import zipfile

    try:
        with zipfile.ZipFile(BytesIO(data)) as z:
            media = [
                n
                for n in z.namelist()
                if n.startswith("word/media/")
                and n.lower().endswith((".png", ".jpg", ".jpeg", ".bmp", ".gif"))
            ][:12]
            images = [(f"({n.rsplit('/', 1)[-1]})", z.read(n)) for n in media]
    except Exception:
        images = []
    sized: list[tuple[str, bytes]] = []
    for where, raw in images:
        try:
            from PIL import Image

            w, h = Image.open(BytesIO(raw)).size
        except Exception:
            continue
        if w >= MIN_DIAGRAM[0] and h >= MIN_DIAGRAM[1]:
            sized.append((where, raw))
    diagrams, n_diag = _diagram_blocks(sized)
    if diagrams:
        parsed_docx.blocks.append(Block("heading", "Diagrams", level=2))
        parsed_docx.blocks.extend(diagrams)
        parsed_docx.warnings.append(
            f"{n_diag} diagram(s) were read with OCR: only their labels are used, not the arrows or the layout"
        )
    return parsed_docx


# ---------------------------------------------------------------- PDF
class _PageBuilder:
    """Turns one PDF page's lines into ordered blocks (paragraphs, bullet lists, headings, tables)."""

    def __init__(self, page_no: int) -> None:
        self.page_no = page_no
        self.items: list[tuple[float, Block]] = []
        self._para: list[str] = []
        self._list: list[str] = []
        self._y = 0.0

    def add_table(self, y: float, markdown: str) -> None:
        self.items.append((y, Block("table", markdown, page=self.page_no)))

    def add_heading(self, y: float, text: str, level: int) -> None:
        self.flush()
        self.items.append((y, Block("heading", text, level=level, page=self.page_no)))

    def add_line(self, y: float, text: str, indent: float) -> None:
        if BULLET_RE.match(text):
            self._flush_para()
            if not self._list:
                self._y = y
            self._list.append(re.sub(r"^\s*(?:[•◦▪●■\-–*])\s+", "- ", text))
        elif self._list and indent > 60:  # wrapped bullet continuation
            self._list[-1] += " " + text
        else:
            self._flush_list()
            if not self._para:
                self._y = y
            self._para.append(text)

    def end_block(self) -> None:
        if self._para and self._para[-1].endswith((".", ":", "?", "!")):
            self._flush_para()

    def _flush_para(self) -> None:
        if self._para:
            self.items.append(
                (
                    self._y,
                    Block("text", " ".join(self._para).strip(), page=self.page_no),
                )
            )
            self._para = []

    def _flush_list(self) -> None:
        if self._list:
            self.items.append(
                (self._y, Block("list", "\n".join(self._list), page=self.page_no))
            )
            self._list = []

    def flush(self) -> None:
        self._flush_para()
        self._flush_list()

    def blocks(self) -> list[Block]:
        self.flush()
        return [b for _, b in sorted(self.items, key=lambda kv: kv[0])]


_LIGATURES = str.maketrans(
    {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"}
)


def unligature(text: str) -> str:
    """PDF fonts store "fi", "fl"... as single ligature glyphs; a search for "notified" must find them."""
    return text.translate(_LIGATURES)


def _line_text(line: dict[str, Any]) -> str:
    return unligature("".join(s["text"] for s in line["spans"])).strip()


def _parse_pdf_page(
    page: Any, page_no: int, heading_sizes: list[float], repeated: set[str]
) -> list[Block]:
    import pymupdf

    builder = _PageBuilder(page_no)
    table_boxes: list[Any] = []
    try:
        found = list(page.find_tables().tables)
    except Exception:
        found = []
    for tbl in found:
        # A real table has a header and at least one more row, and at least two columns. A single
        # ruled box is a callout or a paragraph; treating it as a table splits its words.
        if tbl.row_count < 2 or tbl.col_count < 2:
            continue
        md = _md_table([[c or "" for c in r] for r in tbl.extract()])
        if md:
            builder.add_table(tbl.bbox[1], md)
            table_boxes.append(pymupdf.Rect(tbl.bbox))

    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        if any(pymupdf.Rect(block["bbox"]).intersects(r) for r in table_boxes):
            continue  # already captured as a table
        for line in block["lines"]:
            text = _line_text(line)
            if not text or re.sub(r"\d+", "#", text) in repeated:
                continue
            size = round(max(s["size"] for s in line["spans"]), 1)
            bold = all(
                "bold" in s["font"].lower() or s["flags"] & 16
                for s in line["spans"]
                if s["text"].strip()
            )
            y, x = line["bbox"][1], line["bbox"][0]
            numbered = numbered_heading(text)
            if (size in heading_sizes and len(text) < 120) or (
                bold and len(text) < 90 and numbered
            ):
                level = (
                    numbered[0]
                    if numbered
                    else heading_sizes.index(size) + 1
                    if size in heading_sizes
                    else 3
                )
                builder.add_heading(y, text, level)
            else:
                builder.add_line(y, text, x)
        builder.end_block()
    return builder.blocks()


def _ocr_scanned_pages(pages: list[Any]) -> tuple[list[int], int]:
    """OCR every page that has no text layer (a scan). Returns (page numbers, dropped line count)."""
    from app.qa.brd import ocr

    done: list[int] = []
    dropped = 0
    for no, page in enumerate(pages, start=1):
        if ocr.page_has_text(page):
            continue
        try:
            kept, bad = ocr.ocr_into_page(page)
        except (
            Exception
        ) as exc:  # no OCR engine / model download failed: say so, keep going
            raise BrdParseError(
                f"page {no} is a scan and OCR is unavailable ({type(exc).__name__})"
            ) from exc
        if kept:
            done.append(no)
            dropped += bad
    return done, dropped


MIN_DIAGRAM = (250, 120)  # smaller images are logos and icons


def _diagram_blocks(images: list[tuple[str, bytes]]) -> tuple[list[Block], int]:
    """Text read from embedded images (flow charts, screen mock-ups). Only the labels are recovered: the
    arrows and the layout are not interpreted, and the parse says so."""
    from app.qa.brd import ocr

    blocks: list[Block] = []
    for where, raw in images:
        try:
            lines = ocr.image_lines(raw)
        except (
            Exception
        ):  # no OCR engine or an unreadable image: the document still parses
            return blocks, len(blocks)
        if len(lines) >= 3:
            blocks.append(
                Block(
                    "text",
                    f"Diagram {where} (text read from an image; arrows and layout are not interpreted): "
                    + "; ".join(lines[:40]),
                )
            )
    return blocks, len(blocks)


def _pdf_images(doc: Any, skip_pages: list[int]) -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = []
    seen: set[int] = set()
    for no, page in enumerate(doc, start=1):
        if no in skip_pages:
            continue
        for img in page.get_images(full=True):
            xref = img[0]
            if xref in seen:
                continue
            seen.add(xref)
            try:
                info = doc.extract_image(xref)
            except Exception:
                continue
            if (
                info.get("width", 0) >= MIN_DIAGRAM[0]
                and info.get("height", 0) >= MIN_DIAGRAM[1]
            ):
                out.append((f"on page {no}", info["image"]))
    return out[:12]


def parse_pdf(data: bytes) -> ParsedDoc:
    import pymupdf

    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise BrdParseError(
            f"could not read the PDF file: {type(exc).__name__}"
        ) from exc
    if doc.needs_pass:
        raise BrdParseError("the PDF is password protected")

    pages: list[Any] = [doc.load_page(i) for i in range(doc.page_count)]
    ocr_pages, ocr_dropped = _ocr_scanned_pages(pages)

    # Pass 1: the body font size, and header/footer lines that repeat across pages.
    sizes: Counter[float] = Counter()
    edges: Counter[str] = Counter()
    for page in pages:
        h = page.rect.height
        for b in page.get_text("dict")["blocks"]:
            for line in b.get("lines", []):
                text = _line_text(line)
                if not text:
                    continue
                for s in line["spans"]:
                    sizes[round(s["size"], 1)] += len(s["text"])
                if line["bbox"][1] < h * 0.07 or line["bbox"][1] > h * 0.93:
                    edges[re.sub(r"\d+", "#", text)] += 1
    if not sizes:
        raise BrdParseError(
            "no text could be read from the PDF, not even by OCR (is it blank or an unreadable image?)"
        )
    body = sizes.most_common(1)[0][0]
    repeated = {
        t
        for t, n in edges.items()
        if doc.page_count >= 3 and n >= max(3, doc.page_count * 0.4)
    }
    heading_sizes = sorted((s for s in sizes if s >= body * 1.12), reverse=True)

    blocks: list[Block] = []
    for page_no, page in enumerate(pages, start=1):
        blocks.extend(_parse_pdf_page(page, page_no, heading_sizes, repeated))
    diagrams, n_diag = _diagram_blocks(_pdf_images(doc, ocr_pages))
    if diagrams:
        blocks.append(Block("heading", "Diagrams", level=2))
        blocks.extend(diagrams)
    parsed = ParsedDoc(blocks=blocks, page_count=doc.page_count, format="pdf")
    if n_diag:
        parsed.warnings.append(
            f"{n_diag} diagram(s) were read with OCR: only their labels are used, not the arrows or the layout"
        )
    if ocr_pages:
        parsed.warnings.append(
            f"{len(ocr_pages)} page(s) had no text layer and were read with OCR "
            f"(page {', '.join(map(str, ocr_pages[:8]))}"
            f"{'…' if len(ocr_pages) > 8 else ''}); recognition can be wrong, so check the "
            f"requirements against the original"
            + (f" ({ocr_dropped} unclear line(s) were left out)" if ocr_dropped else "")
        )
    raw = unligature(" ".join(page.get_text("text") for page in pages))
    cov = word_coverage(raw, "\n".join(b.text for b in blocks), ignore=repeated)
    if cov < MIN_COVERAGE:
        parsed.warnings.append(
            f"only {cov:.0%} of the text on the pages was recognised; some content (for example "
            "inside complex tables or columns) may be missing"
        )
    return parsed


def parse_bytes(data: bytes, filename: str) -> ParsedDoc:
    if not data:
        raise BrdParseError("the file is empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise BrdParseError("the file is larger than 20 MB")
    fmt = detect_format(data, filename)
    parsed = {"pdf": parse_pdf, "docx": parse_docx, "text": parse_text}[fmt](data)
    if not any(b.kind != "heading" for b in parsed.blocks):
        raise BrdParseError("no readable content was found in the document")
    return parsed
