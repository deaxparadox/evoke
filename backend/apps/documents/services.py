"""Upload rules. Views call this; they hold no logic of their own (notepad rule 1)."""

import os
import uuid

from django.conf import settings
from django.db import transaction

from .models import File, Job, SegmentRow
from .queue import enqueue_ingest, enqueue_retry
from .storage import storage_key

PDF = "application/pdf"


class UploadError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def create_upload(user, upload, storage) -> File:
    """Check, store and register one uploaded PDF. Queues ingest only after the database commit."""
    name = os.path.basename(upload.name or "")[:255]
    if not name.lower().endswith(".pdf") or upload.content_type != PDF:
        raise UploadError(415, "Only PDF files are accepted.")
    if upload.size > settings.MAX_UPLOAD_BYTES:
        raise UploadError(413, f"File is larger than {settings.MAX_UPLOAD_BYTES} bytes.")
    data = upload.read()
    if len(data) > settings.MAX_UPLOAD_BYTES:  # the size actually received, not only the one declared
        raise UploadError(413, f"File is larger than {settings.MAX_UPLOAD_BYTES} bytes.")
    with transaction.atomic():
        # The key needs the file's id, so the row is made first with a throwaway key.
        file = File.objects.create(owner=user, name=name, media_type=PDF, size_bytes=len(data),
                                   storage_key=f"pending/{uuid.uuid4()}")
        file.storage_key = storage_key(user.pk, file.pk)
        file.save(update_fields=["storage_key"])
        storage.put(file.storage_key, data)  # if this fails the row is rolled back: no file without bytes
        job = Job.objects.create(file=file, kind=Job.Kind.INGEST)
        transaction.on_commit(lambda: enqueue_ingest(job.pk))
    return file


class RetryRefused(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def start_retry(file: File, page: int, adapter: str | None) -> Job:
    """Queue a retry of one page, unless the file already has a job queued or running."""
    with transaction.atomic():
        File.objects.select_for_update().get(pk=file.pk)  # two retry requests for one file take turns here
        if not SegmentRow.objects.filter(file=file, index=page).exists():
            raise RetryRefused(404, f"Page {page} does not exist.")
        if Job.objects.filter(file=file, status__in=[Job.Status.QUEUED, Job.Status.RUNNING]).exists():
            raise RetryRefused(409, "This file has a job queued or running; wait for it to finish.")
        job = Job.objects.create(file=file, kind=Job.Kind.RETRY_PAGE)
        transaction.on_commit(lambda: enqueue_retry(job.pk, page, adapter))
    return job
