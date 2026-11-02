"""Domain types. Pure data and rules: no vendor, framework or I/O imports.

Conventions (fixed once): pages are 1-based; boxes use a top-left origin and
points as the unit. Adapters convert, because PDF libraries disagree.
"""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = 1


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Format(StrEnum):
    PDF = "pdf"
    IMAGE = "image"
    AUDIO = "audio"


class ContentKind(StrEnum):
    TEXT_LAYER = "text_layer"
    SCANNED = "scanned"
    MIXED = "mixed"


class SegmentStatus(StrEnum):
    PENDING = "pending"
    PARSED = "parsed"
    FAILED = "failed"
    RETRYING = "retrying"


class BlockKind(StrEnum):
    PARAGRAPH = "paragraph"
    HEADING = "heading"
    TABLE = "table"
    FIGURE = "figure"
    LIST = "list"
    FORMULA = "formula"
    SPEECH = "speech"


class File(_Model):
    """The raw upload's identity. The bytes live in object storage, not here."""

    file_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)


class BBox(_Model):
    x0: float = Field(ge=0)
    y0: float = Field(ge=0)
    x1: float
    y1: float

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if not (self.x0 < self.x1 and self.y0 < self.y1):
            raise ValueError("bbox needs x0 < x1 and y0 < y1")
        return self


class Location(_Model):
    """Where something came from: a page (with optional box) or a time range."""

    file_id: str = Field(min_length=1)
    page: int | None = Field(default=None, ge=1)
    bbox: BBox | None = None
    t_start: float | None = Field(default=None, ge=0)
    t_end: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _page_xor_time(self) -> Self:
        has_time = self.t_start is not None or self.t_end is not None
        if has_time and (self.t_start is None or self.t_end is None):
            raise ValueError("t_start and t_end go together")
        if has_time == (self.page is not None):
            raise ValueError("location needs a page or a time range, not both or neither")
        if self.bbox is not None and self.page is None:
            raise ValueError("bbox needs a page")
        if has_time and self.t_start > self.t_end:
            raise ValueError("t_start must not be after t_end")
        return self


class Block(_Model):
    id: str = Field(min_length=1)
    kind: BlockKind
    text: str = Field(min_length=1)  # tables are markdown
    location: Location
    confidence: float | None = Field(default=None, ge=0, le=1)
    produced_by: str = Field(min_length=1)  # adapter name and version


class Segment(_Model):
    """A page, a standalone image, or an audio span. `index` is 1-based."""

    index: int = Field(ge=1)
    content_kind: ContentKind | None = None  # only PDF pages have one
    status: SegmentStatus = SegmentStatus.PENDING
    error: str | None = None
    attempts: int = Field(default=0, ge=0)
    adapters_tried: list[str] = []
    blocks: list[Block] = []
    # Page size in points, if known. Lets us check that boxes sit inside the page.
    width: float | None = Field(default=None, gt=0)
    height: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.status is SegmentStatus.FAILED and not self.error:
            raise ValueError("a failed segment needs an error")
        for b in self.blocks:
            box = b.location.bbox
            if box and self.width and self.height and (box.x1 > self.width or box.y1 > self.height):
                raise ValueError(f"block {b.id} box is outside the page")
        return self


class Document(_Model):
    """One fully parsed file: an ordered list of segments."""

    file_id: str = Field(min_length=1)
    format: Format
    schema_version: int = SCHEMA_VERSION
    segments: list[Segment] = []

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        idx = [s.index for s in self.segments]
        if idx != sorted(set(idx)):
            raise ValueError("segment indexes must be unique and in order")
        for s in self.segments:
            for b in s.blocks:
                if b.location.file_id != self.file_id:
                    raise ValueError(f"block {b.id} points at another file")
                if self.format is Format.PDF and b.location.page != s.index:
                    raise ValueError(f"block {b.id} is not on its segment's page")
        return self


class Chunk(_Model):
    """Retrieval-sized text built from blocks of one segment. Not part of the Document."""

    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    locations: list[Location] = Field(min_length=1)

    @model_validator(mode="after")
    def _one_file(self) -> Self:
        if len({loc.file_id for loc in self.locations}) != 1:
            raise ValueError("a chunk's locations must all be in one file")
        return self


class Citation(_Model):
    """Points from an answer to the chunk that supports it. The model cites chunk ids only."""

    chunk_id: str = Field(min_length=1)
    location: Location
