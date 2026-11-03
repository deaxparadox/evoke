"""Each adapter is assigned to a variable typed as its port. mypy fails here if a signature does not match.

Run by `uv run mypy` (and in CI). Under pytest it only builds the objects.
"""

from ragling.formats.pdf.pdfplumber_parser import PdfplumberParser
from ragling.formats.pdf.pypdfium2_parser import Pypdfium2Parser
from ragling.ports import Embedder, Parser, VectorStore
from ragling.testing.fakes import FakeEmbedder, FakeParser, InMemoryVectorStore


def test_adapters_match_their_ports() -> None:
    parsers: list[Parser] = [FakeParser(), PdfplumberParser(), Pypdfium2Parser()]
    embedder: Embedder = FakeEmbedder()
    store: VectorStore = InMemoryVectorStore()
    assert parsers and embedder and store
