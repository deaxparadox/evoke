import pytest

from ragling.formats.pdf.pdfplumber_parser import PdfplumberParser
from ragling.testing.contracts import ParserContract
from tests.golden import PDF_BYTES, TRUTH


class TestPdfplumberContract(ParserContract):
    def make_parser(self): return PdfplumberParser()
    def sample_data(self): return PDF_BYTES
    def sample_pages(self): return len(TRUTH["pages"])


@pytest.mark.parametrize("page", [1, 2, 3])
def test_text_matches_ground_truth(page):
    seg = PdfplumberParser().parse_page(PDF_BYTES, page, "f1")
    assert [b.text for b in seg.blocks] == TRUTH["pages"][page - 1]["paragraphs"]


def test_origin_is_top_left_in_points():
    seg = PdfplumberParser().parse_page(PDF_BYTES, 1, "f1")
    assert (seg.width, seg.height) == tuple(TRUTH["page_size"])
    first, second = (b.location.bbox for b in seg.blocks)
    assert first.y0 < 100 < seg.height / 2          # first paragraph is near the TOP of the page
    assert first.y1 < second.y0                      # reading order goes downward
    assert first.x0 == pytest.approx(72, abs=1)      # left margin is 72 points


def test_blank_page_has_no_blocks():
    assert PdfplumberParser().parse_page(PDF_BYTES, 3, "f1").blocks == []


def test_garbage_bytes_raise_not_silently_empty():
    with pytest.raises(Exception):  # noqa: B017 - pdfplumber/pdfminer raise their own error types
        PdfplumberParser().page_count(b"this is not a pdf")
