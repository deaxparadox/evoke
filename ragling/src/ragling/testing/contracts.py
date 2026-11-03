"""Contract suites: what every adapter of a port must do. Imports pytest, so only tests import this module."""

import math
from collections.abc import Sequence

import pytest

from ragling.core import Chunk, Location


class ParserContract:
    """Subclass and implement `make_parser`, `sample_data` and `sample_pages`."""

    def make_parser(self): raise NotImplementedError

    def sample_data(self) -> bytes: raise NotImplementedError

    def sample_pages(self) -> int: raise NotImplementedError

    def test_has_name_and_version(self):
        p = self.make_parser()
        assert p.name and p.version

    def test_page_count(self):
        assert self.make_parser().page_count(self.sample_data()) == self.sample_pages()

    def test_every_page_is_valid(self):
        p, data = self.make_parser(), self.sample_data()
        for page in range(1, self.sample_pages() + 1):
            seg = p.parse_page(data, page, "f1")
            assert seg.index == page
            for b in seg.blocks:
                assert b.text.strip()
                assert b.location.file_id == "f1" and b.location.page == page
                assert b.produced_by == f"{p.name}@{p.version}"
                box = b.location.bbox
                if box and seg.width and seg.height:
                    assert box.x1 <= seg.width and box.y1 <= seg.height

    def test_blocks_are_in_reading_order(self):
        p, data = self.make_parser(), self.sample_data()
        for page in range(1, self.sample_pages() + 1):
            tops = [b.location.bbox.y0 for b in p.parse_page(data, page, "f1").blocks if b.location.bbox]
            assert tops == sorted(tops)

    @pytest.mark.parametrize("bad", [0, -1, 10_000])
    def test_out_of_range_page_raises(self, bad):
        with pytest.raises(ValueError):
            self.make_parser().parse_page(self.sample_data(), bad, "f1")


class EmbedderContract:
    def make_embedder(self): raise NotImplementedError

    def test_identity(self):
        e = self.make_embedder()
        assert e.model_name and e.dimensions > 0

    def test_documents_have_right_shape_and_finite_values(self):
        e = self.make_embedder()
        vectors = e.embed_documents(["alpha", "beta"])
        assert len(vectors) == 2
        assert all(len(v) == e.dimensions and all(math.isfinite(x) for x in v) for v in vectors)

    def test_empty_input_gives_empty_output(self):
        assert self.make_embedder().embed_documents([]) == []

    def test_same_text_same_vector_different_text_different_vector(self):
        e = self.make_embedder()
        a1, a2, b = e.embed_documents(["alpha", "alpha", "beta"])
        assert a1 == pytest.approx(a2)
        assert a1 != pytest.approx(b)

    def test_query_has_right_shape(self):
        e = self.make_embedder()
        assert len(e.embed_query("alpha")) == e.dimensions


def _chunk(file_id: str, page: int, n: int, text: str | None = None) -> Chunk:
    return Chunk(id=f"{file_id}:p{page}:c{n}", text=text or f"text {file_id} {page} {n}", locations=[Location(file_id=file_id, page=page)])


class VectorStoreContract:
    """Uses 3-dimensional vectors; a store whose column size is fixed must accept them or override `dims`."""

    dims = 3
    model = "m1"

    def make_store(self): raise NotImplementedError

    def v(self, *xs: float) -> Sequence[float]:
        padded = list(xs) + [0.0] * (self.dims - len(xs))
        return padded[: self.dims]

    def test_nearest_first_and_k_limits(self):
        s = self.make_store()
        s.upsert("u1", [(_chunk("f", 1, 1), self.v(1, 0)), (_chunk("f", 1, 2), self.v(0, 1)), (_chunk("f", 1, 3), self.v(1, 1))], self.model)
        hits = s.search("u1", self.v(1, 0), self.model, 2)
        assert [h.chunk_id for h in hits] == ["f:p1:c1", "f:p1:c3"]
        assert hits[0].score >= hits[1].score

    def test_search_on_empty_store_is_empty(self):
        assert self.make_store().search("u1", self.v(1), self.model, 5) == []

    def test_owner_never_sees_other_owners_rows(self):
        s = self.make_store()
        s.upsert("u1", [(_chunk("f", 1, 1), self.v(1, 0))], self.model)
        assert s.search("u2", self.v(1, 0), self.model, 5) == []

    def test_other_model_name_is_not_mixed(self):
        s = self.make_store()
        s.upsert("u1", [(_chunk("f", 1, 1), self.v(1, 0))], self.model)
        assert s.search("u1", self.v(1, 0), "other-model", 5) == []

    def test_upsert_replaces_same_chunk_id(self):
        s = self.make_store()
        s.upsert("u1", [(_chunk("f", 1, 1, "old"), self.v(1, 0))], self.model)
        s.upsert("u1", [(_chunk("f", 1, 1, "new"), self.v(1, 0))], self.model)
        hits = s.search("u1", self.v(1, 0), self.model, 5)
        assert [h.text for h in hits] == ["new"]

    def test_delete_page_only_removes_that_page(self):
        s = self.make_store()
        s.upsert("u1", [(_chunk("f", 1, 1), self.v(1, 0)), (_chunk("f", 2, 1), self.v(1, 0))], self.model)
        s.delete_page("u1", "f", 1)
        assert [h.chunk_id for h in s.search("u1", self.v(1, 0), self.model, 5)] == ["f:p2:c1"]

    def test_delete_file_only_removes_that_file(self):
        s = self.make_store()
        s.upsert("u1", [(_chunk("f", 1, 1), self.v(1, 0)), (_chunk("g", 1, 1), self.v(1, 0))], self.model)
        s.delete_file("u1", "f")
        assert [h.chunk_id for h in s.search("u1", self.v(1, 0), self.model, 5)] == ["g:p1:c1"]

    def test_delete_does_not_touch_other_owners(self):
        s = self.make_store()
        s.upsert("u1", [(_chunk("f", 1, 1), self.v(1, 0))], self.model)
        s.upsert("u2", [(_chunk("f", 1, 1), self.v(1, 0))], self.model)
        s.delete_file("u1", "f")
        s.delete_page("u1", "f", 1)
        assert len(s.search("u2", self.v(1, 0), self.model, 5)) == 1
