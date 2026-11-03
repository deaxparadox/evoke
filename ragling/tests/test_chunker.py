from itertools import pairwise

import pytest

from ragling.core import Block, BlockKind, Location, Segment, SegmentStatus
from ragling.formats.pdf.pdfplumber_parser import PdfplumberParser
from ragling.pipeline.chunker import chunk_segment
from tests.golden import PDF_BYTES


def seg(*texts: str, page: int = 1, file_id: str = "f1", status: SegmentStatus = SegmentStatus.PARSED) -> Segment:
    blocks = [
        Block(id=f"{file_id}:p{page}:b{i}", kind=BlockKind.PARAGRAPH, text=t, location=Location(file_id=file_id, page=page), produced_by="t@1")
        for i, t in enumerate(texts, start=1)
    ]
    return Segment(index=page, status=status, blocks=blocks)


def words(n: int, prefix: str = "w") -> str:
    return " ".join(f"{prefix}{i}" for i in range(1, n + 1))


def test_one_short_block_is_one_chunk():
    chunks = chunk_segment(seg("hello brown world"), max_words=10, overlap_words=2)
    assert [(c.id, c.text) for c in chunks] == [("f1:p1:c1", "hello brown world")]
    assert [loc.page for loc in chunks[0].locations] == [1]


def test_long_block_is_split_with_exact_overlap_and_full_coverage():
    chunks = chunk_segment(seg(words(25)), max_words=10, overlap_words=3)
    parts = [c.text.split() for c in chunks]
    assert all(len(p) <= 10 for p in parts)
    for a, b in pairwise(parts):
        assert a[-3:] == b[:3]                      # neighbours share exactly 3 words
    assert parts[0][0] == "w1" and parts[-1][-1] == "w25"
    covered = {w for p in parts for w in p}
    assert covered == {f"w{i}" for i in range(1, 26)}  # no word is lost


def test_no_overlap_gives_disjoint_chunks():
    parts = [c.text.split() for c in chunk_segment(seg(words(20)), max_words=10, overlap_words=0)]
    assert [len(p) for p in parts] == [10, 10]
    assert set(parts[0]).isdisjoint(parts[1])


def test_exact_multiple_has_no_extra_trailing_chunk():
    assert len(chunk_segment(seg(words(10)), max_words=10, overlap_words=3)) == 1


def test_chunk_across_blocks_has_both_locations_and_paragraph_break():
    s = seg("alpha beta", "gamma delta")
    (chunk,) = chunk_segment(s, max_words=10, overlap_words=0)
    assert chunk.text == "alpha beta\n\ngamma delta"
    assert chunk.locations == [b.location for b in s.blocks]


def test_chunk_inside_one_block_has_only_that_location():
    s = seg(words(12), "other words here")
    first = chunk_segment(s, max_words=5, overlap_words=0)[0]
    assert first.locations == [s.blocks[0].location]


def test_a_chunk_never_crosses_a_page():
    p1, p2 = seg(words(7, "a"), page=1), seg(words(7, "b"), page=2)
    for s in (p1, p2):
        for c in chunk_segment(s, max_words=5, overlap_words=2):
            assert {loc.page for loc in c.locations} == {s.index}
            assert c.id.startswith(f"f1:p{s.index}:")
    assert not any("b1" in c.text for c in chunk_segment(p1, 5, 2))  # text of page 2 never leaks into page 1


def test_ids_are_sequential_and_stable_across_runs():
    s = seg(words(30))
    first = [c.id for c in chunk_segment(s, max_words=8, overlap_words=2)]
    assert first == [f"f1:p1:c{n}" for n in range(1, len(first) + 1)]
    assert first == [c.id for c in chunk_segment(s, max_words=8, overlap_words=2)]


@pytest.mark.parametrize("status", [SegmentStatus.PENDING, SegmentStatus.FAILED, SegmentStatus.RETRYING])
def test_unparsed_segments_give_no_chunks(status):
    s = seg("some text", status=status) if status is not SegmentStatus.FAILED else Segment(index=1, status=status, error="boom")
    assert chunk_segment(s, max_words=10, overlap_words=0) == []


def test_empty_segment_and_whitespace_only_block_give_no_chunks():
    assert chunk_segment(Segment(index=1, status=SegmentStatus.PARSED), 10, 0) == []
    assert chunk_segment(seg("   "), 10, 0) == []


@pytest.mark.parametrize("max_words, overlap", [(0, 0), (-1, 0), (5, -1), (5, 5), (5, 9)])
def test_bad_sizes_are_refused(max_words, overlap):
    with pytest.raises(ValueError):
        chunk_segment(seg("a b c"), max_words=max_words, overlap_words=overlap)


def test_real_parser_output_on_the_golden_pdf():
    parser = PdfplumberParser()
    page1 = chunk_segment(parser.parse_page(PDF_BYTES, 1, "f1"), max_words=100, overlap_words=10)
    assert [c.text for c in page1] == [
        "The quick brown fox jumps over the lazy dog. It was a sunny day.\n\nSecond paragraph on page one."
    ]
    assert [loc.page for loc in page1[0].locations] == [1, 1]
    assert chunk_segment(parser.parse_page(PDF_BYTES, 3, "f1"), 100, 10) == []  # blank page
