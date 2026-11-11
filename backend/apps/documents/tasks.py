"""Background ingestion. Tasks receive ids and re-read everything from the database (they never trust a caller's owner).

`ragling` does the real work (parse, chunk, embed); these functions only move data between it, Postgres and S3.
"""

import logging

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from ragling.core.models import SegmentStatus
from ragling.pipeline.chunker import chunk_segment

from . import wiring
from .models import File, Job, SegmentRow

logger = logging.getLogger(__name__)
DONE = (Job.Status.COMPLETED, Job.Status.COMPLETED_WITH_ERRORS, Job.Status.FAILED)


def _begin(job_id: int, kind: str) -> Job | None:
    """Mark the job running. Returns None when it already finished (a repeated delivery does nothing)."""
    with transaction.atomic():
        job = Job.objects.select_for_update().select_related("file").get(pk=job_id)
        if job.kind != kind:
            raise ValueError(f"job {job_id} is a {job.kind} job, not {kind}")
        if job.status in DONE:
            return None
        job.status = Job.Status.RUNNING  # a job found already running is resumed: an earlier run crashed
        job.started_at = job.started_at or timezone.now()
        job.save(update_fields=["status", "started_at"])
    return job


def _finish(job: Job, status: str, error: str = "") -> None:
    job.status, job.error, job.finished_at = status, error[:2000], timezone.now()
    job.save(update_fields=["status", "error", "finished_at"])


def _load_pdf(file: File, storage) -> bytes:
    data = storage.get(file.storage_key)
    if not data.startswith(b"%PDF-"):  # the upload only checked name and type; this is the real check
        raise ValueError("the stored file is not a PDF")
    return data


def _process_page(file: File, data: bytes, page: int, parser) -> bool:
    """Parse, chunk, embed and save one page. A page that fails is marked failed and never raises. True on success."""
    owner_id, file_id = str(file.owner_id), str(file.pk)
    row = SegmentRow.objects.get(file=file, index=page)
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
        return True
    except SoftTimeLimitExceeded:
        raise
    except Exception as e:
        logger.exception("file %s page %s failed", file.pk, page)
        SegmentRow.objects.filter(pk=row.pk).update(
            status=SegmentStatus.FAILED.value, error=f"{type(e).__name__}: {e}"[:2000], attempts=row.attempts + 1,
            adapters_tried=[*row.adapters_tried, parser.name],
        )
        return False


@shared_task(name="documents.ingest_file")
def ingest_file(job_id: int) -> None:
    job = _begin(job_id, Job.Kind.INGEST)
    if job is None:
        return
    file = job.file
    try:
        data = _load_pdf(file, wiring.get_storage())
        parser = wiring.get_parser()
        count = parser.page_count(data)
        if count < 1:
            raise ValueError("the PDF has no pages")
        SegmentRow.objects.bulk_create(
            [SegmentRow(file=file, index=i) for i in range(1, count + 1)], ignore_conflicts=True
        )
        todo = SegmentRow.objects.filter(file=file).exclude(status=SegmentStatus.PARSED.value).order_by("index").values_list("index", flat=True)
        for page in list(todo):
            _process_page(file, data, page, parser)
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


@shared_task(name="documents.retry_page")
def retry_page(job_id: int, page: int, adapter: str | None = None) -> None:
    job = _begin(job_id, Job.Kind.RETRY_PAGE)
    if job is None:
        return
    file = job.file
    try:
        data = _load_pdf(file, wiring.get_storage())
        parser = wiring.get_parser(adapter)
        ok = _process_page(file, data, page, parser)
    except SoftTimeLimitExceeded:
        _finish(job, Job.Status.FAILED, "time limit reached")
        return
    except Exception as e:
        logger.exception("retry of file %s page %s failed", file.pk, page)
        _finish(job, Job.Status.FAILED, f"{type(e).__name__}: {e}")
        return
    _finish(job, Job.Status.COMPLETED if ok else Job.Status.COMPLETED_WITH_ERRORS, "" if ok else f"page {page} failed again")
