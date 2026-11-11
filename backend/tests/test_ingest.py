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

    def __init__(self, fail=(), crash=(), crash_on_open=False):
        self.real, self.fail, self.crash, self.parsed = PdfplumberParser(), set(fail), set(crash), []
        self.crash_on_open, self.calls = crash_on_open, []  # `calls` records every attempt, including ones that crash

    def page_count(self, data):
        self.calls.append("open")
        if self.crash_on_open:
            raise Crash
        return self.real.page_count(data)

    def parse_page(self, data, page, file_id):
        self.calls.append(page)
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


def test_a_page_that_kills_the_worker_is_given_up_and_the_rest_continue(alice, storage, use_parser):
    spy = use_parser(SpyParser(crash=[2]))  # page 2 crashes the process every time it is tried
    job = make_job(alice, storage)
    with pytest.raises(Crash):
        tasks.ingest_file(job.pk)
    job.refresh_from_db()
    assert job.status == Job.Status.RUNNING and job.current_page == 2
    assert [s.status for s in pages(job).values()] == ["parsed", "pending", "pending"]
    tasks.ingest_file(job.pk)  # the broker delivers the task again
    job.refresh_from_db()
    segs = pages(job)
    assert job.status == Job.Status.COMPLETED_WITH_ERRORS and job.current_page is None
    assert (segs[1].status, segs[2].status, segs[3].status) == ("parsed", "failed", "parsed")
    assert "worker process stopped" in segs[2].error and segs[2].attempts == 1
    assert spy.calls.count(2) == 1  # tried once, never again
    assert spy.calls.count(1) == 1  # a parsed page is not parsed again


def test_a_crash_that_happens_every_delivery_stops_at_the_cap(alice, storage, use_parser, settings):
    settings.TASK_MAX_DELIVERIES = 4
    spy = use_parser(SpyParser(crash_on_open=True))  # dies before any page: there is no page to blame
    job = make_job(alice, storage)
    for _ in range(10):  # what the broker would do: send it again and again
        try:
            tasks.ingest_file(job.pk)
        except Crash:
            pass
    job.refresh_from_db()
    assert spy.calls == ["open"] * 4
    assert job.status == Job.Status.FAILED and "stopped 4 times" in job.error and job.finished_at
    assert job.deliveries == 5  # the fifth delivery is the one that gave up; later ones do not count


def test_the_cap_is_exact(alice, storage, use_parser, settings):
    settings.TASK_MAX_DELIVERIES = 2
    spy = use_parser(SpyParser(crash_on_open=True))
    job = make_job(alice, storage)
    for _ in range(2):
        with pytest.raises(Crash):
            tasks.ingest_file(job.pk)
    assert spy.calls == ["open", "open"]  # exactly the cap still runs
    tasks.ingest_file(job.pk)  # one over: no crash, no parse, job failed
    assert spy.calls == ["open", "open"]
    assert Job.objects.get(pk=job.pk).status == Job.Status.FAILED


def test_a_crash_before_any_page_is_resumed_in_full_when_the_worker_comes_back(alice, storage, use_parser):
    use_parser(SpyParser(crash_on_open=True))
    job = make_job(alice, storage)
    with pytest.raises(Crash):
        tasks.ingest_file(job.pk)
    healthy = use_parser(SpyParser())
    tasks.ingest_file(job.pk)
    assert Job.objects.get(pk=job.pk).status == Job.Status.COMPLETED and healthy.parsed == [1, 2, 3]


def test_the_page_marker_is_cleared_after_an_ordinary_failure_even_on_the_last_page(alice, storage, use_parser):
    use_parser(SpyParser(fail=[3]))  # last page: no later success can clear the marker by accident
    job = make_job(alice, storage)
    tasks.ingest_file(job.pk)
    assert Job.objects.get(pk=job.pk).current_page is None


def test_a_retry_that_kills_the_worker_does_not_loop(alice, storage, use_parser):
    use_parser(SpyParser(fail=[2]))
    first = make_job(alice, storage)
    tasks.ingest_file(first.pk)
    retry = Job.objects.create(file=first.file, kind=Job.Kind.RETRY_PAGE)
    spy = use_parser(SpyParser(crash=[2]))
    with pytest.raises(Crash):
        tasks.retry_page(retry.pk, 2)
    tasks.retry_page(retry.pk, 2)  # redelivered
    retry.refresh_from_db()
    assert retry.status == Job.Status.COMPLETED_WITH_ERRORS and "worker process stopped" in retry.error
    assert spy.calls == [2]  # the second delivery did not touch the parser
    seg = pages(first)[2]
    assert seg.status == "failed" and "worker process stopped" in seg.error and seg.attempts == 2  # ingest try + the retry that died


def test_a_pdf_with_too_many_pages_is_refused_before_any_page_is_made(alice, storage, use_parser, settings):
    settings.MAX_PDF_PAGES = 2
    use_parser(SpyParser())
    job = make_job(alice, storage)  # 3 pages
    tasks.ingest_file(job.pk)
    job.refresh_from_db()
    assert job.status == Job.Status.FAILED and "3 pages; the limit is 2" in job.error
    assert not SegmentRow.objects.exists() and not ChunkRow.objects.exists()


def test_a_pdf_at_the_page_limit_is_accepted(alice, storage, use_parser, settings):
    settings.MAX_PDF_PAGES = 3
    use_parser(SpyParser())
    job = make_job(alice, storage)
    tasks.ingest_file(job.pk)
    assert Job.objects.get(pk=job.pk).status == Job.Status.COMPLETED


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


# ---- a task Celery gives up on (BUG-20261111-2211) ----

@pytest.fixture
def closed_connections(monkeypatch):
    """The hook closes the database connection; inside a test transaction that must not really happen."""
    closed = []
    monkeypatch.setattr(tasks.connection, "close", lambda: closed.append(True))
    return closed


@pytest.mark.parametrize("status", [Job.Status.RUNNING, Job.Status.QUEUED])
def test_a_job_killed_by_the_hard_time_limit_is_closed_as_failed(alice, storage, closed_connections, settings, status):
    from celery.exceptions import TimeLimitExceeded
    settings.CELERY_TASK_TIME_LIMIT = 900
    job = make_job(alice, storage, status=status)
    tasks.ingest_file.on_failure(TimeLimitExceeded(900), "task-id", [job.pk], {}, None)
    job.refresh_from_db()
    assert job.status == Job.Status.FAILED and job.finished_at
    assert "time limit exceeded (900 s)" in job.error and "stay pending" in job.error


def test_other_failures_are_named_in_the_job_error(alice, storage, closed_connections):
    job = make_job(alice, storage, status=Job.Status.RUNNING)
    tasks.retry_page.on_failure(MemoryError("boom"), "task-id", [], {"job_id": job.pk}, None)  # job id given as keyword
    job.refresh_from_db()
    assert job.status == Job.Status.FAILED and "MemoryError: boom" in job.error


@pytest.mark.parametrize("final", [Job.Status.COMPLETED, Job.Status.COMPLETED_WITH_ERRORS, Job.Status.FAILED])
def test_a_job_that_already_finished_is_left_alone(alice, storage, closed_connections, final):
    job = make_job(alice, storage, status=final)
    Job.objects.filter(pk=job.pk).update(error="original")
    tasks.ingest_file.on_failure(RuntimeError("late"), "task-id", [job.pk], {}, None)
    job.refresh_from_db()
    assert job.status == final and job.error == "original"


def test_the_hook_never_raises_and_always_closes_the_connection(storage, closed_connections):
    tasks.ingest_file.on_failure(RuntimeError("x"), "task-id", [999999], {}, None)  # no such job
    tasks.ingest_file.on_failure(RuntimeError("x"), "task-id", [], {}, None)  # no job id at all
    assert closed_connections == [True, True]


def test_a_failed_job_no_longer_blocks_retries(alice, storage, closed_connections):
    job = make_job(alice, storage, status=Job.Status.RUNNING)
    SegmentRow.objects.create(file=job.file, index=1, status="pending")
    assert api(alice).post(f"/api/files/{job.file.pk}/pages/1/retry").status_code == 409
    tasks.ingest_file.on_failure(RuntimeError("killed"), "task-id", [job.pk], {}, None)
    assert api(alice).post(f"/api/files/{job.file.pk}/pages/1/retry").status_code == 202


def test_both_tasks_use_the_hook_and_celery_really_calls_it(alice, storage, closed_connections):
    assert all(isinstance(t, tasks.IngestTask) for t in (tasks.ingest_file, tasks.retry_page))
    job = make_job(alice, storage, kind=Job.Kind.RETRY_PAGE)  # a retry job handed to ingest_file: raises ValueError
    result = tasks.ingest_file.apply(args=[job.pk])  # runs through Celery's own error path
    assert result.failed()
    job.refresh_from_db()
    assert job.status == Job.Status.FAILED and "ValueError" in job.error


def test_a_database_error_inside_the_hook_is_logged_not_raised_and_the_connection_still_closes(alice, storage, closed_connections, monkeypatch, caplog):
    job = make_job(alice, storage, status=Job.Status.RUNNING)

    def broken(*a, **k):
        raise RuntimeError("database is gone")

    monkeypatch.setattr(tasks.Job.objects, "filter", broken)
    tasks.ingest_file.on_failure(RuntimeError("original failure"), "task-id", [job.pk], {}, None)  # must not raise
    assert closed_connections == [True]
    assert "could not close job" in caplog.text
