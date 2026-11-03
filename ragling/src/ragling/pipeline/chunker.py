"""Build chunks from the blocks of one parsed page.

A chunk never crosses a page break, because the input is a single Segment (notepad section 14).
Size is counted in words as a stand-in for tokens; exact token counts come with the evals.
"""

from ragling.core import Chunk, Location, Segment, SegmentStatus


def chunk_segment(segment: Segment, max_words: int, overlap_words: int) -> list[Chunk]:
    """Slide a window of `max_words` words over the page's blocks, `overlap_words` shared between neighbours.

    Only parsed segments produce chunks. Chunk ids are `{file_id}:p{page}:c{n}` (n from 1), so a retried page
    writes the same ids. A chunk's locations are those of every block it draws words from, in order.
    """
    if max_words < 1:
        raise ValueError("max_words must be at least 1")
    if not 0 <= overlap_words < max_words:
        raise ValueError("overlap_words must be at least 0 and less than max_words")
    if segment.status is not SegmentStatus.PARSED:
        return []

    words: list[tuple[str, int]] = [(w, i) for i, b in enumerate(segment.blocks) for w in b.text.split()]
    if not words:
        return []
    file_id = segment.blocks[0].location.file_id

    chunks: list[Chunk] = []
    start, step = 0, max_words - overlap_words
    while True:
        end = min(start + max_words, len(words))
        window = words[start:end]
        used: list[int] = []  # block indexes in order, no repeats
        for _, block_index in window:
            if not used or used[-1] != block_index:
                used.append(block_index)
        paragraphs = [" ".join(w for w, i in window if i == block_index) for block_index in used]
        locations: list[Location] = [segment.blocks[i].location for i in used]
        chunks.append(Chunk(id=f"{file_id}:p{segment.index}:c{len(chunks) + 1}", text="\n\n".join(paragraphs), locations=locations))
        if end == len(words):
            return chunks
        start += step
