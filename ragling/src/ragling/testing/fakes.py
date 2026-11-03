"""Fakes for tests: deterministic, no model, no database."""

import hashlib
import math
from collections.abc import Sequence

from ragling.core import Block, BlockKind, Chunk, Location, Segment, SegmentStatus
from ragling.ports import Hit


class FakeEmbedder:
    """Vector comes from a hash of the text: same text, same vector; different texts differ."""

    def __init__(self, dimensions: int = 8):
        self.model_name = "fake-embedder"
        self.dimensions = dimensions

    def _vec(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode()).digest()
        raw = [digest[i % len(digest)] - 127.5 for i in range(self.dimensions)]
        norm = math.sqrt(sum(x * x for x in raw))
        return [x / norm for x in raw]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


class InMemoryVectorStore:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str, str, str], tuple[Chunk, list[float]]] = {}

    @staticmethod
    def _file_page(chunk: Chunk) -> tuple[str, int | None]:
        loc = chunk.locations[0]
        return loc.file_id, loc.page

    def upsert(self, owner_id: str, items: Sequence[tuple[Chunk, Sequence[float]]], model_name: str) -> None:
        for chunk, vector in items:
            file_id, _ = self._file_page(chunk)
            self._rows[(owner_id, model_name, file_id, chunk.id)] = (chunk, list(vector))

    def search(self, owner_id: str, vector: Sequence[float], model_name: str, k: int) -> list[Hit]:
        def cosine(a: Sequence[float], b: Sequence[float]) -> float:
            dot = sum(x * y for x, y in zip(a, b, strict=True))
            return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))

        hits = [
            Hit(chunk_id=c.id, text=c.text, locations=c.locations, score=cosine(vector, v))
            for (owner, model, _, _), (c, v) in self._rows.items()
            if owner == owner_id and model == model_name
        ]
        return sorted(hits, key=lambda h: h.score, reverse=True)[:k]

    def delete_page(self, owner_id: str, file_id: str, page: int) -> None:
        for key in [k for k, (c, _) in self._rows.items() if k[0] == owner_id and self._file_page(c) == (file_id, page)]:
            del self._rows[key]

    def delete_file(self, owner_id: str, file_id: str) -> None:
        for key in [k for k in self._rows if k[0] == owner_id and k[2] == file_id]:
            del self._rows[key]


class FakeParser:
    """Treats the bytes as text where form feeds (\\f) separate pages and blank lines paragraphs."""

    name = "fake-parser"
    version = "1"

    def page_count(self, data: bytes) -> int:
        return len(data.decode().split("\f"))

    def parse_page(self, data: bytes, page: int, file_id: str) -> Segment:
        pages = data.decode().split("\f")
        if not 1 <= page <= len(pages):
            raise ValueError(f"page {page} out of range 1..{len(pages)}")
        paragraphs = [p.strip() for p in pages[page - 1].split("\n\n") if p.strip()]
        blocks = [
            Block(
                id=f"{file_id}:p{page}:b{i}",
                kind=BlockKind.PARAGRAPH,
                text=text,
                location=Location(file_id=file_id, page=page),
                produced_by=f"{self.name}@{self.version}",
            )
            for i, text in enumerate(paragraphs, start=1)
        ]
        return Segment(index=page, status=SegmentStatus.PARSED, blocks=blocks)
