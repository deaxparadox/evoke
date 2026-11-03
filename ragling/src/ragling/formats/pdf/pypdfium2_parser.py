"""PDF text parser adapter on pypdfium2 (BSD-3 / Apache-2.0). Needs the `pdf-pdfium` extra."""

from importlib.metadata import version as _dist_version

from ragling.core import Segment, SegmentStatus
from ragling.formats.pdf._layout import build_blocks


class Pypdfium2Parser:
    name = "pypdfium2"

    def __init__(self):
        self.version = _dist_version("pypdfium2")

    def page_count(self, data: bytes) -> int:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(data)
        try:
            return len(pdf)
        finally:
            pdf.close()

    def parse_page(self, data: bytes, page: int, file_id: str) -> Segment:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(data)
        try:
            if not 1 <= page <= len(pdf):
                raise ValueError(f"page {page} out of range 1..{len(pdf)}")
            p = pdf[page - 1]
            width, height = p.get_size()
            textpage = p.get_textpage()
            lines = []
            for i in range(textpage.count_rects()):
                # pdfium rectangles are (left, bottom, right, top) with the origin at the bottom left.
                left, bottom, right, top = textpage.get_rect(i)
                text = textpage.get_text_bounded(left, bottom, right, top).strip()
                if text:
                    lines.append({"text": text, "x0": left, "x1": right, "top": height - top, "bottom": height - bottom})
            lines.sort(key=lambda line: (line["top"], line["x0"]))
            return Segment(
                index=page,
                status=SegmentStatus.PARSED,
                blocks=build_blocks(lines, file_id, page, f"{self.name}@{self.version}"),
                width=float(width),
                height=float(height),
            )
        finally:
            pdf.close()
