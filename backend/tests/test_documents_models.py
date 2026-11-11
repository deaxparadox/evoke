import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from ragling.core.models import ContentKind, SegmentStatus

from apps.documents.models import EMBEDDING_DIMENSIONS, ChunkRow, File, Job, SegmentRow

pytestmark = pytest.mark.django_db


def vec(x=0.1):
    return [x] * EMBEDDING_DIMENSIONS


@pytest.fixture
def user():
    return get_user_model().objects.create_user("alice", password="pw")


@pytest.fixture
def file(user):
    return File.objects.create(owner=user, name="a.pdf", media_type="application/pdf", size_bytes=10, storage_key="k/a")


def make_chunk(file, n=1, page=1, **kw):
    fields = {"owner": file.owner, "file": file, "page": page, "chunk_id": f"f:p{page}:c{n}", "text": "t",
              "embedding_model": "voyage-4-nano", "embedding": vec()}
    return ChunkRow.objects.create(**{**fields, **kw})


def test_pgvector_extension_is_enabled():
    with connection.cursor() as c:
        c.execute("select 1 from pg_extension where extname = 'vector'")
        assert c.fetchone()


def test_vector_roundtrip_and_model_name_kept(file):
    make_chunk(file, embedding=[float(i % 5) for i in range(EMBEDDING_DIMENSIONS)])
    row = ChunkRow.objects.get()
    assert list(row.embedding)[:6] == [0.0, 1.0, 2.0, 3.0, 4.0, 0.0]
    assert row.embedding_model == "voyage-4-nano"


def test_wrong_vector_size_is_rejected(file):
    with pytest.raises(Exception), transaction.atomic():  # noqa: B017 (driver error type varies)
        make_chunk(file, embedding=[0.1] * 3)


def test_one_segment_per_page(file):
    SegmentRow.objects.create(file=file, index=1)
    with pytest.raises(IntegrityError), transaction.atomic():
        SegmentRow.objects.create(file=file, index=1)


def test_segment_index_starts_at_1(file):
    with pytest.raises(IntegrityError), transaction.atomic():
        SegmentRow.objects.create(file=file, index=0)


def test_segment_defaults_match_ragling_and_blocks_store_json(file):
    s = SegmentRow.objects.create(file=file, index=1, content_kind=ContentKind.TEXT_LAYER.value,
                                  blocks=[{"id": "b1", "text": "hi"}])
    s.refresh_from_db()
    assert s.status == SegmentStatus.PENDING.value and s.attempts == 0 and s.adapters_tried == []
    assert s.blocks == [{"id": "b1", "text": "hi"}]


def test_one_chunk_id_per_file_but_same_id_allowed_in_another_file(file, user):
    make_chunk(file)
    other = File.objects.create(owner=user, name="b.pdf", media_type="application/pdf", size_bytes=1, storage_key="k/b")
    make_chunk(other)  # same chunk_id text, other file: fine
    with pytest.raises(IntegrityError), transaction.atomic():
        make_chunk(file)


def test_storage_key_is_unique(file, user):
    with pytest.raises(IntegrityError), transaction.atomic():
        File.objects.create(owner=user, name="c", media_type="x", size_bytes=1, storage_key="k/a")


def test_job_defaults_to_queued(file):
    assert Job.objects.create(file=file, kind=Job.Kind.INGEST).status == Job.Status.QUEUED


def test_deleting_a_file_deletes_its_pages_chunks_and_jobs(file):
    SegmentRow.objects.create(file=file, index=1)
    make_chunk(file)
    Job.objects.create(file=file, kind=Job.Kind.INGEST)
    file.delete()
    assert not (SegmentRow.objects.exists() or ChunkRow.objects.exists() or Job.objects.exists())


def test_deleting_a_user_deletes_everything_they_own(file, user):
    SegmentRow.objects.create(file=file, index=1)
    make_chunk(file)
    user.delete()
    assert not (File.objects.exists() or SegmentRow.objects.exists() or ChunkRow.objects.exists())


def test_chunk_owner_is_required(file):
    with pytest.raises(IntegrityError), transaction.atomic():
        make_chunk(file, owner=None)
