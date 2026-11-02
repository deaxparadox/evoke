import pytest
from pydantic import ValidationError

from ragling.core import (
    BBox,
    Block,
    BlockKind,
    Chunk,
    Citation,
    Document,
    File,
    Format,
    Location,
    Segment,
    SegmentStatus,
)


def loc(page=1, **kw):
    return Location(file_id="f1", page=page, **kw)


def block(page=1, bbox=None, id="b1"):
    return Block(id=id, kind=BlockKind.PARAGRAPH, text="hi", location=loc(page, bbox=bbox), produced_by="x@1")


def test_file_ok_and_negative_size_rejected():
    File(file_id="f1", name="a.pdf", media_type="application/pdf", size_bytes=1)
    with pytest.raises(ValidationError):
        File(file_id="f1", name="a.pdf", media_type="application/pdf", size_bytes=-1)


@pytest.mark.parametrize("kw", [
    {"page": 0},                                   # pages are 1-based
    {},                                         # neither page nor time
    {"page": 1, "t_start": 0, "t_end": 1},               # both
    {"t_start": 0},                                # half a range
    {"t_start": 5, "t_end": 1},                       # backwards
    {"t_start": 0, "t_end": 1, "bbox": BBox(x0=0, y0=0, x1=1, y1=1)},  # bbox without page
])
def test_location_rejects(kw):
    with pytest.raises(ValidationError):
        Location(file_id="f1", **kw)


def test_location_ok():
    loc(3, bbox=BBox(x0=0, y0=0, x1=10, y1=10))
    Location(file_id="f1", t_start=1.5, t_end=3)


@pytest.mark.parametrize("box", [{"x0": 5, "y0": 0, "x1": 5, "y1": 1}, {"x0": -1, "y0": 0, "x1": 1, "y1": 1}])
def test_bbox_rejects(box):
    with pytest.raises(ValidationError):
        BBox(**box)


def test_block_needs_text_and_valid_confidence():
    with pytest.raises(ValidationError):
        Block(id="b", kind=BlockKind.PARAGRAPH, text="", location=loc(), produced_by="x@1")
    with pytest.raises(ValidationError):
        Block(id="b", kind=BlockKind.PARAGRAPH, text="t", location=loc(), produced_by="x@1", confidence=1.5)


def test_failed_segment_needs_error():
    with pytest.raises(ValidationError):
        Segment(index=1, status=SegmentStatus.FAILED)
    Segment(index=1, status=SegmentStatus.FAILED, error="boom")


def test_box_must_be_inside_known_page():
    inside = BBox(x0=0, y0=0, x1=100, y1=100)
    outside = BBox(x0=0, y0=0, x1=700, y1=100)
    Segment(index=1, width=612, height=792, blocks=[block(bbox=inside)])
    with pytest.raises(ValidationError):
        Segment(index=1, width=612, height=792, blocks=[block(bbox=outside)])
    Segment(index=1, blocks=[block(bbox=outside)])  # page size unknown: not checked


def test_document_rules():
    Document(file_id="f1", format=Format.PDF, segments=[Segment(index=1, blocks=[block()]), Segment(index=2)])
    with pytest.raises(ValidationError):  # out of order
        Document(file_id="f1", format=Format.PDF, segments=[Segment(index=2), Segment(index=1)])
    with pytest.raises(ValidationError):  # duplicate
        Document(file_id="f1", format=Format.PDF, segments=[Segment(index=1), Segment(index=1)])
    with pytest.raises(ValidationError):  # block on the wrong page
        Document(file_id="f1", format=Format.PDF, segments=[Segment(index=1, blocks=[block(page=2)])])
    other = Block(id="b", kind=BlockKind.PARAGRAPH, text="t", location=Location(file_id="zzz", page=1), produced_by="x@1")
    with pytest.raises(ValidationError):  # block from another file
        Document(file_id="f1", format=Format.PDF, segments=[Segment(index=1, blocks=[other])])


def test_document_json_round_trip():
    d = Document(file_id="f1", format=Format.PDF, segments=[Segment(index=1, blocks=[block()])])
    assert d.schema_version == 1
    assert Document.model_validate_json(d.model_dump_json()) == d


def test_chunk_rules():
    Chunk(id="c", text="t", locations=[loc(1), loc(1)])
    with pytest.raises(ValidationError):
        Chunk(id="c", text="t", locations=[])
    with pytest.raises(ValidationError):
        Chunk(id="c", text="t", locations=[loc(1), Location(file_id="other", page=1)])


def test_citation_and_unknown_fields():
    Citation(chunk_id="c", location=loc())
    with pytest.raises(ValidationError):
        Citation(chunk_id="c", location=loc(), page=3)
