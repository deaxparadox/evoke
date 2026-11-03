"""Shared by the PDF parser adapters: turn extracted text lines into paragraph blocks.

A line is a dict with `text`, `x0`, `x1`, `top`, `bottom`, in points with a top-left origin,
listed from the top of the page down. Each adapter only has to produce such lines.
"""

from ragling.core import BBox, Block, BlockKind, Location

# A line starts a new paragraph when the gap above it is bigger than this share of the line height.
PARAGRAPH_GAP_RATIO = 0.6


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


def build_blocks(lines: list[dict], file_id: str, page: int, produced_by: str) -> list[Block]:
    return [
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
            produced_by=produced_by,
        )
        for i, group in enumerate(_paragraphs(lines), start=1)
    ]
