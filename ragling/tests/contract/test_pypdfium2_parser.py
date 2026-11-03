import pytest

from ragling.formats.pdf.pdfplumber_parser import PdfplumberParser
from ragling.formats.pdf.pypdfium2_parser import Pypdfium2Parser
from ragling.testing.contracts import ParserContract
from tests.golden import PDF_BYTES, TRUTH


class TestPypdfium2Contract(ParserContract):
    def make_parser(self): return Pypdfium2Parser()
    def sample_data(self): return PDF_BYTES
    def sample_pages(self): return len(TRUTH["pages"])


@pytest.mark.parametrize("page", [1, 2, 3])
def test_text_matches_ground_truth(page):
    seg = Pypdfium2Parser().parse_page(PDF_BYTES, page, "f1")
    assert [b.text for b in seg.blocks] == TRUTH["pages"][page - 1]["paragraphs"]


def test_origin_is_converted_to_top_left():
    seg = Pypdfium2Parser().parse_page(PDF_BYTES, 1, "f1")
    assert (seg.width, seg.height) == tuple(TRUTH["page_size"])
    first, second = (b.location.bbox for b in seg.blocks)
    assert first.y0 < 100 < seg.height / 2       # first paragraph is near the TOP (pdfium itself counts from the bottom)
    assert first.y1 < second.y0
    assert first.x0 == pytest.approx(72, abs=1.5)


def test_blank_page_has_no_blocks():
    assert Pypdfium2Parser().parse_page(PDF_BYTES, 3, "f1").blocks == []


def test_garbage_bytes_raise_not_silently_empty():
    with pytest.raises(Exception):  # noqa: B017 - pypdfium2 raises its own error type
        Pypdfium2Parser().page_count(b"this is not a pdf")


@pytest.mark.parametrize("page", [1, 2])
def test_both_adapters_agree_on_text_and_roughly_on_boxes(page):
    a = PdfplumberParser().parse_page(PDF_BYTES, page, "f1")
    b = Pypdfium2Parser().parse_page(PDF_BYTES, page, "f1")
    assert [x.text for x in a.blocks] == [x.text for x in b.blocks]
    for x, y in zip(a.blocks, b.blocks, strict=True):
        bx, by = x.location.bbox, y.location.bbox
        for field in ("x0", "y0", "x1", "y1"):
            assert getattr(bx, field) == pytest.approx(getattr(by, field), abs=3), field  # different engines measure a line slightly differently
