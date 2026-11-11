import pytest
from celery import current_app
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from ragling.formats.pdf.pdfplumber_parser import PdfplumberParser
from ragling.testing.fakes import FakeEmbedder
from ragling.testing.pdf import make_text_pdf
from rest_framework.test import APIClient

from apps.documents import queue, tasks, wiring
from apps.documents.models import EMBEDDING_DIMENSIONS, ChunkRow, File, Job, SegmentRow
from apps.documents.storage import InMemoryStorage, storage_key
from config.celery import check_worker_config

pytestmark = pytest.mark.django_db
PAGES = [["Alpha page one."], ["Beta page two."], ["Gamma page three."]]


class Crash(BaseException):
    """Stands in for a killed worker: not an Exception, so nothing in the task catches it."""


class SpyParser:
    name = "spy"
    version = "1"

    def __init__(self, fail=(), crash=()):
        self.real, self.fail, self.crash, self.parsed = PdfplumberParser(), set(fail), set(crash), []

    def page_count(self, data):
        return self.real.page_count(data)

    def parse_page(self, data, page, file_id):
        if page in self.crash:
            raise Crash
        if page in self.fail:
            raise RuntimeError(f"cannot read page {page}")
        self.parsed.append(page)
        return self.real.parse_page(data, page, file_id)


@pytest.fixture
def storage(monkeypatch):
    s = InMemoryStorage()
    monkeypatch.setattr(wiring, "get_storage", lambda: s)
    return s


@pytest.fixture(autouse=True)
def fake_embedder(monkeypatch):
    emb = FakeEmbedder(dimensions=EMBEDDING_DIMENSIONS)
    monkeypatch.setattr(wiring, "get_embedder", lambda: emb)
    return emb


@pytest.fixture
def use_parser(monkeypatch):
    def use(parser):
        monkeypatch.setattr(wiring, "get_parser", lambda name=None: parser)
        return parser
    return use


@pytest.fixture
def alice():
    return get_user_model().objects.create_user("alice", password="pw")


def make_job(owner, storage, data=None, kind=Job.Kind.INGEST, status=Job.Status.QUEUED):
    f = File.objects.create(owner=owner, name="a.pdf", media_type="application/pdf", size_bytes=1, storage_key=f"pending/{owner.pk}/{File.objects.count()}")
    f.storage_key = storage_key(owner.pk, f.pk)
    f.save()
    if data is not False:
        storage.put(f.storage_key, make_text_pdf(PAGES) if data is None else data)
    return Job.objects.create(file=f, kind=kind, status=status)


def pages(job):
    return {r.index: r for r in SegmentRow.objects.filter(file=job.file)}


# ---- ingest_file ----

def test_ingest_parses_chunks_embeds_and_stores_every_page(alice, storage, use_parser):
    use_parser(SpyParser())
    job = make_job(alice, storage)
    tasks.ingest_file(job.pk)
    job.refresh_from_db()
    assert job.status == Job.Status.COMPLETED and job.finished_at and job.started_at
    segs = pages(job)
    assert sorted(segs) == [1, 2, 3] and all(s.status == "parsed" and s.attempts == 1 for s in segs.values())
    assert segs[1].blocks and segs[1].adapters_tried == ["spy"] and segs[1].width
    chunks = ChunkRow.objects.filter(file=job.file)
    assert {c.page for c in chunks} == {1, 2, 3}
    assert all(c.owner_id == alice.pk and c.embedding_model == "fake-embedder" for c in chunks)
    assert "Beta page two." in ChunkRow.objects.get(file=job.file, page=2).text


def test_one_failing_page_is_marked_and_the_rest_continue(alice, storage, use_parser):
    use_parser(SpyParser(fail=[2]))
    job = make_job(alice, storage)
    tasks.ingest_file(job.pk)
    job.refresh_from_db()
    segs = pages(job)
    assert job.status == Job.Status.COMPLETED_WITH_ERRORS and "1 pages failed" in job.error
    assert (segs[1].status, segs[2].status, segs[3].status) == ("parsed", "failed", "parsed")
    assert "cannot read page 2" in segs[2].error and segs[2].attempts == 1
    assert set(ChunkRow.objects.filter(file=job.file).values_list("page", flat=True)) == {1, 3}


def test_a_crash_leaves_finished_pages_saved_and_a_rerun_resumes(alice, storage, use_parser):
    use_parser(SpyParser(crash=[2]))
    job = make_job(alice, storage)
    with pytest.raises(Crash):
        tasks.ingest_file(job.pk)
    job.refresh_from_db()
    assert job.status == Job.Status.RUNNING
    assert [s.status for s in pages(job).values()] == ["parsed", "pending", "pending"]
    resumed = use_parser(SpyParser())
    tasks.ingest_file(job.pk)  # the broker delivers the task again
    job.refresh_from_db()
    assert job.status == Job.Status.COMPLETED
    assert resumed.parsed == [2, 3]  # page 1 was not parsed again
    assert ChunkRow.objects.filter(file=job.file).count() == len({c.chunk_id for c in ChunkRow.objects.filter(file=job.file)})


def test_a_finished_job_delivered_again_does_nothing(alice, storage, use_parser):
    spy = use_parser(SpyParser())
    job = make_job(alice, storage)
    tasks.ingest_file(job.pk)
    before = ChunkRow.objects.count()
    spy.parsed.clear()
    tasks.ingest_file(job.pk)
    assert spy.parsed == [] and ChunkRow.objects.count() == before


def test_reprocessing_a_page_replaces_its_chunks_instead_of_adding(alice, storage, use_parser):
    use_parser(SpyParser())
    job = make_job(alice, storage)
    tasks.ingest_file(job.pk)
    before = sorted(ChunkRow.objects.filter(file=job.file).values_list("chunk_id", flat=True))
    SegmentRow.objects.filter(file=job.file, index=2).update(status="pending")
    Job.objects.filter(pk=job.pk).update(status=Job.Status.QUEUED)
    tasks.ingest_file(job.pk)
    assert sorted(ChunkRow.objects.filter(file=job.file).values_list("chunk_id", flat=True)) == before


def test_a_page_that_now_gives_fewer_chunks_leaves_no_stale_ones(alice, storage, use_parser, settings):
    use_parser(SpyParser())
    long_page = "one two three four five six seven eight nine ten eleven twelve"
    job = make_job(alice, storage, data=make_text_pdf([[long_page]]))
    settings.CHUNK_MAX_WORDS, settings.CHUNK_OVERLAP_WORDS = 4, 0
    tasks.ingest_file(job.pk)
    assert ChunkRow.objects.filter(file=job.file, page=1).count() == 3
    settings.CHUNK_MAX_WORDS = 12  # e.g. a retry with different settings: one chunk now
    SegmentRow.objects.filter(file=job.file).update(status="pending")
    Job.objects.filter(pk=job.pk).update(status=Job.Status.QUEUED)
    tasks.ingest_file(job.pk)
    assert [c.chunk_id for c in ChunkRow.objects.filter(file=job.file, page=1)] == [f"{job.file.pk}:p1:c1"]


@pytest.mark.parametrize("data,expect", [(False, "FileNotFoundError"), (b"hello, not a pdf", "not a PDF"), (b"%PDF-1.4 but broken", "")])
def test_problems_with_the_whole_file_fail_the_job_not_the_task(alice, storage, use_parser, data, expect):
    use_parser(SpyParser())
    job = make_job(alice, storage, data=data)
    tasks.ingest_file(job.pk)  # must not raise
    job.refresh_from_db()
    assert job.status == Job.Status.FAILED and expect in job.error and job.finished_at
    assert not ChunkRow.objects.exists()


def test_a_job_of_the_wrong_kind_is_refused(alice, storage):
    job = make_job(alice, storage, kind=Job.Kind.RETRY_PAGE)
    with pytest.raises(ValueError, match="retry_page"):
        tasks.ingest_file(job.pk)


# ---- retry_page ----

def test_retry_page_fixes_a_failed_page_and_keeps_the_others(alice, storage, use_parser):
    use_parser(SpyParser(fail=[2]))
    job = make_job(alice, storage)
    tasks.ingest_file(job.pk)
    chunks_p1 = list(ChunkRow.objects.filter(file=job.file, page=1).values_list("pk", flat=True))
    retry = Job.objects.create(file=job.file, kind=Job.Kind.RETRY_PAGE)
    use_parser(SpyParser())
    tasks.retry_page(retry.pk, 2)
    retry.refresh_from_db()
    seg2 = pages(job)[2]
    assert retry.status == Job.Status.COMPLETED
    assert seg2.status == "parsed" and seg2.error == "" and seg2.attempts == 2 and seg2.adapters_tried == ["spy", "spy"]
    assert ChunkRow.objects.filter(file=job.file, page=2).exists()
    assert list(ChunkRow.objects.filter(file=job.file, page=1).values_list("pk", flat=True)) == chunks_p1  # untouched


def test_retry_page_that_fails_again_says_so(alice, storage, use_parser):
    use_parser(SpyParser(fail=[2]))
    job = make_job(alice, storage)
    tasks.ingest_file(job.pk)
    retry = Job.objects.create(file=job.file, kind=Job.Kind.RETRY_PAGE)
    tasks.retry_page(retry.pk, 2)
    retry.refresh_from_db()
    assert retry.status == Job.Status.COMPLETED_WITH_ERRORS and pages(job)[2].attempts == 2


def test_retry_page_uses_the_adapter_it_was_given(alice, storage, monkeypatch):
    asked = []
    spy = SpyParser()
    monkeypatch.setattr(wiring, "get_parser", lambda name=None: asked.append(name) or spy)
    job = make_job(alice, storage)
    tasks.ingest_file(job.pk)
    retry = Job.objects.create(file=job.file, kind=Job.Kind.RETRY_PAGE)
    tasks.retry_page(retry.pk, 1, "pypdfium2")
    assert asked == [None, "pypdfium2"]


# ---- retry API: refused while a job is queued or running ----

def api(user):
    c = APIClient()
    c.force_login(user)
    return c


@pytest.mark.parametrize("busy", [Job.Status.QUEUED, Job.Status.RUNNING])
def test_retry_is_refused_while_a_job_is_queued_or_running(alice, storage, busy):
    job = make_job(alice, storage, status=busy)
    SegmentRow.objects.create(file=job.file, index=1, status="failed", error="x")
    r = api(alice).post(f"/api/files/{job.file.pk}/pages/1/retry")
    assert r.status_code == 409
    assert Job.objects.filter(file=job.file).count() == 1


def test_retry_is_accepted_when_idle_and_queued_only_after_commit(alice, storage, monkeypatch, django_capture_on_commit_callbacks):
    sent = []
    monkeypatch.setattr("apps.documents.services.enqueue_retry", lambda *a: sent.append(a))
    job = make_job(alice, storage, status=Job.Status.COMPLETED_WITH_ERRORS)
    SegmentRow.objects.create(file=job.file, index=2, status="failed", error="x")
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        r = api(alice).post(f"/api/files/{job.file.pk}/pages/2/retry", {"adapter": "pypdfium2"}, format="json")
    assert r.status_code == 202
    new = Job.objects.get(pk=r.json()["job_id"])
    assert (new.kind, new.status, new.file) == (Job.Kind.RETRY_PAGE, Job.Status.QUEUED, job.file)
    assert sent == []
    for cb in callbacks:
        cb()
    assert sent == [(new.pk, 2, "pypdfium2")]


def test_retry_of_missing_page_other_users_file_or_unknown_adapter(alice, storage):
    bob = get_user_model().objects.create_user("bob", password="pw")
    job = make_job(alice, storage, status=Job.Status.COMPLETED)
    SegmentRow.objects.create(file=job.file, index=1)
    assert api(alice).post(f"/api/files/{job.file.pk}/pages/9/retry").status_code == 404
    assert api(bob).post(f"/api/files/{job.file.pk}/pages/1/retry").status_code == 404
    assert api(alice).post(f"/api/files/{job.file.pk}/pages/1/retry", {"adapter": "nope"}, format="json").status_code == 400
    assert Job.objects.filter(file=job.file).count() == 1


# ---- queue and wiring ----

def test_a_down_broker_marks_the_job_failed_instead_of_breaking_the_request(alice, storage, monkeypatch):
    def broken(*a, **k):
        raise ConnectionError("redis is down")

    monkeypatch.setattr(tasks.ingest_file, "delay", broken)
    job = make_job(alice, storage)
    queue.enqueue_ingest(job.pk)
    job.refresh_from_db()
    assert job.status == Job.Status.FAILED and "could not queue" in job.error and "redis is down" in job.error


def test_sending_through_celery_runs_the_registered_task(alice, storage, use_parser):
    """Eager mode: `.delay` runs the task in-process, which proves the task name and arguments line up."""
    use_parser(SpyParser())
    app = current_app._get_current_object()
    app.conf.task_always_eager = True
    try:
        job = make_job(alice, storage)
        queue.enqueue_ingest(job.pk)
    finally:
        app.conf.task_always_eager = False
    job.refresh_from_db()
    assert job.status == Job.Status.COMPLETED


def test_worker_boot_fails_without_the_model_revision(settings):
    settings.EMBED_MODEL_REVISION = ""
    with pytest.raises(ImproperlyConfigured, match="EMBED_MODEL_REVISION"):
        check_worker_config()
    settings.EMBED_MODEL_REVISION = "abc123"
    check_worker_config()


def test_unknown_parser_name_fails_loudly(settings):
    settings.PDF_PARSER = "nope"
    with pytest.raises(ImproperlyConfigured, match="nope"):
        wiring.get_parser()


def test_time_limits_are_ordered_and_visibility_timeout_is_longer(settings):
    assert settings.CELERY_TASK_SOFT_TIME_LIMIT < settings.CELERY_TASK_TIME_LIMIT
    assert settings.CELERY_BROKER_TRANSPORT_OPTIONS["visibility_timeout"] > settings.CELERY_TASK_TIME_LIMIT
