"""The fakes must themselves pass the contract suites, or the suites prove nothing."""

from ragling.testing.contracts import (
    EmbedderContract,
    ParserContract,
    VectorStoreContract,
)
from ragling.testing.fakes import FakeEmbedder, FakeParser, InMemoryVectorStore

SAMPLE = b"First paragraph.\n\nSecond paragraph.\fPage two text.\f"


class TestFakeParser(ParserContract):
    def make_parser(self): return FakeParser()
    def sample_data(self): return SAMPLE
    def sample_pages(self): return 3


class TestFakeEmbedder(EmbedderContract):
    def make_embedder(self): return FakeEmbedder()


class TestInMemoryVectorStore(VectorStoreContract):
    def make_store(self): return InMemoryVectorStore()
