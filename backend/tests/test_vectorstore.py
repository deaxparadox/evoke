import random

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from ragling.core.models import Chunk, Location
from ragling.ports import VectorStore
from ragling.testing.contracts import VectorStoreContract

from apps.documents.models import EMBEDDING_DIMENSIONS, ChunkRow, File
from apps.documents.vectorstore import PgVectorStore

pytestmark = pytest.mark.django_db


def make_file(owner, name):
    return File.objects.create(owner=owner, name=name, media_type="application/pdf", size_bytes=1, storage_key=f"k/{owner.pk}/{name}")


def make_users_and_files():
    User = get_user_model()
    u1, u2 = User.objects.create_user("u1", password="pw"), User.objects.create_user("u2", password="pw")
    return u1, u2, make_file(u1, "f"), make_file(u1, "g"), make_file(u2, "h")


class TestPgVectorStore(VectorStoreContract):
    dims = EMBEDDING_DIMENSIONS

    def make_store(self):
        u1, u2, f, g, h = make_users_and_files()
        self.u1, self.u2, self.f, self.g, self.h = (str(x.pk) for x in (u1, u2, f, g, h))
        return PgVectorStore()


def test_is_a_vector_store():
    assert isinstance(PgVectorStore(), VectorStore)


def chunk(file, n, text="t"):
    return Chunk(id=f"{file.pk}:p1:c{n}", text=text, locations=[Location(file_id=str(file.pk), page=1)])


def unit(i):
    v = [0.0] * EMBEDDING_DIMENSIONS
    v[i] = 1.0
    return v


def test_cannot_store_under_someone_elses_file():
    _, u2, f, _, _ = make_users_and_files()
    with pytest.raises(ValueError, match="belong"):
        PgVectorStore().upsert(str(u2.pk), [(chunk(f, 1), unit(0))], "m1")  # f belongs to u1
    assert not ChunkRow.objects.exists()


def test_score_is_cosine_similarity_and_locations_survive():
    u1, _, f, _, _ = make_users_and_files()
    s = PgVectorStore()
    s.upsert(str(u1.pk), [(chunk(f, 1), unit(0))], "m1")
    same, = s.search(str(u1.pk), unit(0), "m1", 1)
    assert same.score == pytest.approx(1.0)
    assert same.locations == [Location(file_id=str(f.pk), page=1)]
    other, = s.search(str(u1.pk), unit(1), "m1", 1)
    assert other.score == pytest.approx(0.0)


def test_upsert_keeps_one_row_and_updates_model_name():
    u1, _, f, _, _ = make_users_and_files()
    s = PgVectorStore()
    s.upsert(str(u1.pk), [(chunk(f, 1, "old"), unit(0))], "m1")
    s.upsert(str(u1.pk), [(chunk(f, 1, "new"), unit(1))], "m2")
    row, = ChunkRow.objects.all()
    assert (row.text, row.embedding_model) == ("new", "m2")


def random_vector(rng):
    v = [0.0] * EMBEDDING_DIMENSIONS
    for i in range(16):  # a 16-dimensional cloud: vectors of both owners overlap, as real text vectors do
        v[i] = rng.gauss(0, 1)
    return v


def test_filtered_search_returns_k_rows_when_half_the_nearest_rows_belong_to_someone_else():
    """The reason for iterative scans: without them the owner filter leaves about 20 of the 100 rows asked for."""
    u1, u2, f, _, h = make_users_and_files()
    rng = random.Random(1)
    s = PgVectorStore()
    s.upsert(str(u1.pk), [(chunk(f, i), random_vector(rng)) for i in range(1500)], "m1")
    s.upsert(str(u2.pk), [(chunk(h, i), random_vector(rng)) for i in range(1500)], "m1")
    with connection.cursor() as c:
        c.execute("ANALYZE documents_chunkrow")
        # At this size Postgres would pick a plain scan or the owner index and never touch the HNSW index.
        # Inside this test's transaction (rolled back at the end) take those options away, so the test
        # checks the vector index itself, which is what the planner uses when one owner has many rows.
        c.execute("SET LOCAL enable_seqscan = off")
        c.execute("SET LOCAL enable_bitmapscan = off")
        c.execute("select indexname from pg_indexes where tablename = 'documents_chunkrow' and indexdef like '%(owner_id)%'")
        for (name,) in c.fetchall():
            c.execute(f'DROP INDEX "{name}"')
    for _ in range(3):
        hits = s.search(str(u1.pk), random_vector(rng), "m1", 100)
        assert len(hits) == 100
        assert all(hit.chunk_id.startswith(f"{f.pk}:") for hit in hits)
        assert [x.score for x in hits] == sorted((x.score for x in hits), reverse=True)
