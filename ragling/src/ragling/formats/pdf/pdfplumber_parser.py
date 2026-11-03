"""PDF text parser adapter on pdfplumber (MIT). Needs the `pdf` extra: `pip install ragling[pdf]`."""

from importlib.metadata import version as _dist_version
from io import BytesIO

from ragling.core import BBox, Block, BlockKind, Location, Segment

# A line starts a new paragraph when the gap above it is bigger than this share of the line height.
PARAGRAPH_GAP_RATIO = 0.6


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
            blocks = [
                Block(
                    id=f"{file_id}:p{page}:b{i}",
                    kind=BlockKind.PARAGRAPH,
                    text=" ".join(line["text"] for line in group),
                    location=Location(
                        file_id=file_id,
                        page=page,
                        bbox=BBox(
                            x0=max(0.0, min(line["x0"] for line in group)),
                            y0=max(0.0, min(line["top"] for line in group)),
                            x1=max(line["x1"] for line in group),
                            y1=max(line["bottom"] for line in group),
                        ),
                    ),
                    produced_by=f"{self.name}@{self.version}",
                )
                for i, group in enumerate(_paragraphs(lines), start=1)
            ]
            return Segment(index=page, blocks=blocks, width=float(p.width), height=float(p.height))


def _paragraphs(lines: list[dict]) -> list[list[dict]]:
    groups: list[list[dict]] = []
    for line in lines:
        if groups:
            prev = groups[-1][-1]
            height = prev["bottom"] - prev["top"]
            if line["top"] - prev["bottom"] <= PARAGRAPH_GAP_RATIO * height:
                groups[-1].append(line)
                continue
        groups.append([line])
    return groups
