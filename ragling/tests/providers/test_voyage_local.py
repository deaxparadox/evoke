"""Logic of the adapter, with the model replaced by a stub. The real model is in test_voyage_local_real.py."""

import math

import pytest

from ragling.providers.embed import voyage_local
from ragling.providers.embed.voyage_local import VoyageLocalEmbedder
from ragling.testing.contracts import EmbedderContract


class StubModel:
    """Returns 2048-wide vectors (the model's full size) that are NOT unit length, and records which path was used."""

    def __init__(self):
        self.calls: list[str] = []

    def _vec(self, text: str) -> list[float]:
        return [(len(text) + i % 7 + 1) * 0.5 for i in range(voyage_local.DIMENSIONS)]

    def encode_document(self, texts):
        self.calls.append("document")
        return [self._vec(t) for t in texts]

    def encode_query(self, text):
        self.calls.append("query")
        return self._vec(text)


@pytest.fixture(autouse=True)
def stub_model(monkeypatch):
    stub = StubModel()
    monkeypatch.setattr(voyage_local, "_load_model", lambda revision: stub)
    return stub


class TestVoyageLocalWithStub(EmbedderContract):
    def make_embedder(self): return VoyageLocalEmbedder(revision="abc123")


def test_revision_is_required():
    for bad in ("", "   "):
        with pytest.raises(ValueError, match="revision"):
            VoyageLocalEmbedder(revision=bad)


def test_vectors_are_unit_length(stub_model):
    e = VoyageLocalEmbedder(revision="abc123")
    for v in [*e.embed_documents(["alpha", "be"]), e.embed_query("alpha")]:
        assert math.isclose(math.sqrt(sum(x * x for x in v)), 1.0)


def test_documents_and_queries_use_their_own_model_paths(stub_model):
    e = VoyageLocalEmbedder(revision="abc123")
    e.embed_documents(["a"])
    e.embed_query("a")
    assert stub_model.calls == ["document", "query"]


def test_empty_input_does_not_call_the_model(stub_model):
    VoyageLocalEmbedder(revision="abc123").embed_documents([])
    assert stub_model.calls == []


def test_zero_vector_is_an_error():
    with pytest.raises(ValueError, match="zero"):
        voyage_local._unit([0.0, 0.0])


def test_revision_reaches_the_loader(monkeypatch):
    seen = []
    monkeypatch.setattr(voyage_local, "_load_model", lambda revision: seen.append(revision) or StubModel())
    VoyageLocalEmbedder(revision="deadbeef")
    assert seen == ["deadbeef"]
