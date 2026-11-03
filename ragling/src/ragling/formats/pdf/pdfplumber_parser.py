"""PDF text parser adapter on pdfplumber (MIT). Needs the `pdf` extra: `pip install ragling[pdf]`."""

from importlib.metadata import version as _dist_version
from io import BytesIO

from ragling.core import Segment
from ragling.formats.pdf._layout import build_blocks


class PdfplumberParser:
    name = "pdfplumber"

    def __init__(self):
        self.version = _dist_version("pdfplumber")

    def page_count(self, data: bytes) -> int:
        import pdfplumber

        with pdfplumber.open(BytesIO(data)) as pdf:
            return len(pdf.pages)

    def parse_page(self, data: bytes, page: int, file_id: str) -> Segment:
        import pdfplumber

        # ponytail: reopens the file for every page, so a whole-file parse is O(pages) opens; measured in FEAT-20261103-1308.
        with pdfplumber.open(BytesIO(data)) as pdf:
            if not 1 <= page <= len(pdf.pages):
                raise ValueError(f"page {page} out of range 1..{len(pdf.pages)}")
            p = pdf.pages[page - 1]
            lines = p.extract_text_lines(strip=True)  # top-left origin, points, top to bottom
            return Segment(
                index=page,
                blocks=build_blocks(lines, file_id, page, f"{self.name}@{self.version}"),
                width=float(p.width),
                height=float(p.height),
            )
