"""Interfaces the library defines. Adapters (parsers, embedders, stores) implement them.

Ports are `typing.Protocol`s: an adapter needs no base class, only the right methods.
Each port has a shared contract suite in `ragling.testing` that every adapter must pass.
"""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from ragling.core import Chunk, Location, Segment


class Hit(BaseModel):
    """One search result. `score` is cosine similarity: higher is closer."""

    chunk_id: str
    text: str
    locations: list[Location]
    score: float


@runtime_checkable
class Parser(Protocol):
    """Turns one page of a file into a Segment of blocks.

    Page-level on purpose, so one failed page can be retried alone. Image and audio parsers
    will need a different shape; revisit at M4 and M5.
    """

    name: str
    version: str

    def page_count(self, data: bytes) -> int: ...

    def parse_page(self, data: bytes, page: int, file_id: str) -> Segment:
        """`page` is 1-based. Raises ValueError if it is out of range."""
        ...


@runtime_checkable
class Embedder(Protocol):
    model_name: str
    dimensions: int

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


@runtime_checkable
class VectorStore(Protocol):
    """Stores chunks with vectors. Every call is scoped to one owner.

    Vector search only for now; where hybrid search lives is still open (notepad question 7).
    """

    def upsert(self, owner_id: str, items: Sequence[tuple[Chunk, Sequence[float]]], model_name: str) -> None:
        """Insert or replace by (file, chunk id), so a retried page writes the same rows."""
        ...

    def search(self, owner_id: str, vector: Sequence[float], model_name: str, k: int) -> list[Hit]: ...

    def delete_page(self, owner_id: str, file_id: str, page: int) -> None: ...

    def delete_file(self, owner_id: str, file_id: str) -> None: ...
