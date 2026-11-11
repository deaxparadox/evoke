"""Background ingestion. Tasks receive ids and re-read everything from the database (they never trust a caller's owner).

`ragling` does the real work (parse, chunk, embed); these functions only move data between it, Postgres and S3.
"""

import logging

from celery import Task, shared_task
from celery.exceptions import SoftTimeLimitExceeded, TimeLimitExceeded
from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone
from ragling.core.models import SegmentStatus
from ragling.pipeline.chunker import chunk_segment

from . import wiring
from .models import File, Job, SegmentRow

logger = logging.getLogger(__name__)
DONE = (Job.Status.COMPLETED, Job.Status.COMPLETED_WITH_ERRORS, Job.Status.FAILED)


CRASH_MESSAGE = "the worker process stopped while handling this page (crash, out of memory or time limit)"


class IngestTask(Task):
    """Base for both tasks. Closes the job when Celery gives up on the task without our code getting to run.

    After a hard time limit the worker's main process kills the child, acknowledges the message and calls this hook
    (celery/worker/request.py, on_timeout). The job would otherwise stay `running` for ever and block retries.
    """

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        job_id = args[0] if args else kwargs.get("job_id")
        if isinstance(exc, TimeLimitExceeded):
            reason = f"time limit exceeded ({settings.CELERY_TASK_TIME_LIMIT} s); pages not done stay pending"
        else:
            reason = f"{type(exc).__name__}: {exc}"
        try:
            Job.objects.filter(pk=job_id, status__in=[Job.Status.QUEUED, Job.Status.RUNNING]).update(
                status=Job.Status.FAILED, error=reason[:2000], finished_at=timezone.now()
            )
        except Exception:  # a hook that raises would hide the real failure
            logger.exception("could not close job %s after its task failed", job_id)
        finally:
            # After a hard timeout this runs in the worker's main process, which forks the replacement child: a
            # connection left open here would be shared with it.
            connection.close()


def _begin(job_id: int, kind: str) -> tuple[Job | None, int | None]:
    """Mark the job running. Returns (job, recovered_page).

    The job is None when there is nothing to do: it already finished (a repeated delivery does nothing), or it has been
    picked up too often. `recovered_page` is the page an earlier run died on, now marked failed, if there was one.
    """
    with transaction.atomic():
        job = Job.objects.select_for_update().select_related("file").get(pk=job_id)
        if job.kind != kind:
            raise ValueError(f"job {job_id} is a {job.kind} job, not {kind}")
        if job.status in DONE:
            return None, None
        recovered = None
        if job.status == Job.Status.RUNNING and job.current_page is not None:
            # An earlier run died on this page (it never cleared the marker). Give it up, so the file can go on.
            recovered = job.current_page
            row = SegmentRow.objects.filter(file=job.file, index=recovered).first()
            if row:
                SegmentRow.objects.filter(pk=row.pk).update(
                    status=SegmentStatus.FAILED.value, error=CRASH_MESSAGE, attempts=row.attempts + 1
                )
            job.current_page = None
        job.deliveries += 1
        if job.deliveries > settings.TASK_MAX_DELIVERIES:
            job.status, job.finished_at = Job.Status.FAILED, timezone.now()
            job.error = f"the worker stopped {job.deliveries - 1} times while running this job; giving up"
            job.save(update_fields=["status", "finished_at", "error", "deliveries", "current_page"])
            return None, recovered
        job.status = Job.Status.RUNNING  # a job found already running is resumed: an earlier run crashed
        job.started_at = job.started_at or timezone.now()
        job.save(update_fields=["status", "started_at", "deliveries", "current_page"])
    return job, recovered


def _finish(job: Job, status: str, error: str = "") -> None:
    job.status, job.error, job.finished_at = status, error[:2000], timezone.now()
    job.save(update_fields=["status", "error", "finished_at"])


def _load_pdf(file: File, storage) -> bytes:
    data = storage.get(file.storage_key)
    if not data.startswith(b"%PDF-"):  # the upload only checked name and type; this is the real check
        raise ValueError("the stored file is not a PDF")
    return data


def _process_page(job: Job, data: bytes, page: int, parser) -> bool:
    """Parse, chunk, embed and save one page. A page that fails is marked failed and never raises. True on success."""
    file = job.file
    owner_id, file_id = str(file.owner_id), str(file.pk)
    row = SegmentRow.objects.get(file=file, index=page)
    # Written (and committed) before the risky work. If the process dies, the next run finds it and knows which page did it.
    Job.objects.filter(pk=job.pk).update(current_page=page)
    try:
        segment = parser.parse_page(data, page, file_id)
        chunks = chunk_segment(segment, settings.CHUNK_MAX_WORDS, settings.CHUNK_OVERLAP_WORDS)
        embedder = wiring.get_embedder()
        vectors = embedder.embed_documents([c.text for c in chunks])
        with transaction.atomic():  # the page's chunks and its status change together or not at all
            store = wiring.get_store()
            store.delete_page(owner_id, file_id, page)  # replaces anything an earlier try left
            store.upsert(owner_id, list(zip(chunks, vectors, strict=True)), embedder.model_name)
            row.content_kind = segment.content_kind.value if segment.content_kind else None
            row.status = SegmentStatus.PARSED.value
            row.error = ""
            row.blocks = [b.model_dump(mode="json") for b in segment.blocks]
            row.width, row.height = segment.width, segment.height
            row.adapters_tried = [*row.adapters_tried, parser.name]
            row.attempts += 1
            row.save()
            Job.objects.filter(pk=job.pk).update(current_page=None)
        return True
    except SoftTimeLimitExceeded:
        raise
    except Exception as e:
        logger.exception("file %s page %s failed", file.pk, page)
        SegmentRow.objects.filter(pk=row.pk).update(
            status=SegmentStatus.FAILED.value, error=f"{type(e).__name__}: {e}"[:2000], attempts=row.attempts + 1,
            adapters_tried=[*row.adapters_tried, parser.name],
        )
        Job.objects.filter(pk=job.pk).update(current_page=None)
        return False


@shared_task(base=IngestTask, name="documents.ingest_file")
def ingest_file(job_id: int) -> None:
    job, _ = _begin(job_id, Job.Kind.INGEST)
    if job is None:
        return
    file = job.file
    try:
        data = _load_pdf(file, wiring.get_storage())
        parser = wiring.get_parser()
        count = parser.page_count(data)
        if count < 1:
            raise ValueError("the PDF has no pages")
        if count > settings.MAX_PDF_PAGES:
            raise ValueError(f"the PDF has {count} pages; the limit is {settings.MAX_PDF_PAGES}")
        SegmentRow.objects.bulk_create(
            [SegmentRow(file=file, index=i) for i in range(1, count + 1)], ignore_conflicts=True
        )
        # Only pages nobody has tried yet. A failed page (including one a dead worker was stuck on) waits for an explicit retry.
        todo = SegmentRow.objects.filter(file=file, status=SegmentStatus.PENDING.value).order_by("index").values_list("index", flat=True)
        for page in list(todo):
            _process_page(job, data, page, parser)
    except SoftTimeLimitExceeded:
        left = SegmentRow.objects.filter(file=file).exclude(status__in=[SegmentStatus.PARSED.value, SegmentStatus.FAILED.value]).count()
        _finish(job, Job.Status.FAILED, f"time limit reached; {left} pages not done, they stay pending")
        return
    except Exception as e:  # a problem with the whole file, not one page
        logger.exception("ingest of file %s failed", file.pk)
        _finish(job, Job.Status.FAILED, f"{type(e).__name__}: {e}")
        return
    failed = SegmentRow.objects.filter(file=file, status=SegmentStatus.FAILED.value).count()
    _finish(job, Job.Status.COMPLETED_WITH_ERRORS if failed else Job.Status.COMPLETED,
            f"{failed} pages failed" if failed else "")


@shared_task(base=IngestTask, name="documents.retry_page")
def retry_page(job_id: int, page: int, adapter: str | None = None) -> None:
    job, recovered = _begin(job_id, Job.Kind.RETRY_PAGE)
    if job is None:
        return
    if recovered == page:  # the earlier run died on this very page: do not try it a second time
        _finish(job, Job.Status.COMPLETED_WITH_ERRORS, f"page {page} failed again: {CRASH_MESSAGE}")
        return
    file = job.file
    try:
        data = _load_pdf(file, wiring.get_storage())
        parser = wiring.get_parser(adapter)
        ok = _process_page(job, data, page, parser)
    except SoftTimeLimitExceeded:
        _finish(job, Job.Status.FAILED, "time limit reached")
        return
    except Exception as e:
        logger.exception("retry of file %s page %s failed", file.pk, page)
        _finish(job, Job.Status.FAILED, f"{type(e).__name__}: {e}")
        return
    _finish(job, Job.Status.COMPLETED if ok else Job.Status.COMPLETED_WITH_ERRORS, "" if ok else f"page {page} failed again")
