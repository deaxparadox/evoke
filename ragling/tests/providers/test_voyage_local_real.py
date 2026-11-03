"""Runs the real model. Only on purpose: `EVOKE_EMBED_REVISION=<hash> uv run --extra embed-local pytest -m embed_local`."""

import math
import os

import pytest

from ragling.providers.embed.voyage_local import VoyageLocalEmbedder
from ragling.testing.contracts import EmbedderContract

pytestmark = pytest.mark.embed_local


@pytest.fixture(scope="module")
def embedder():
    revision = os.environ.get("EVOKE_EMBED_REVISION")
    if not revision:  # fail loud: a selected -m embed_local run must never pass by doing nothing
        pytest.fail("EVOKE_EMBED_REVISION is not set")
    return VoyageLocalEmbedder(revision=revision)


class TestVoyageLocalReal(EmbedderContract):
    @pytest.fixture(autouse=True)
    def _inject(self, embedder):
        self._embedder = embedder

    def make_embedder(self): return self._embedder

    def test_vectors_are_unit_length_and_related_text_is_closer(self):
        e = self._embedder
        q = e.embed_query("Which planet is known as the Red Planet?")
        mars, venus = e.embed_documents([
            "Mars, known for its reddish appearance, is often referred to as the Red Planet.",
            "Venus is often called Earth's twin because of its similar size.",
        ])
        assert math.isclose(math.sqrt(sum(x * x for x in q)), 1.0, rel_tol=1e-4)
        dot = lambda a, b: sum(x * y for x, y in zip(a, b))
        assert dot(q, mars) > dot(q, venus)
