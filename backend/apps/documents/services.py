"""Upload rules. Views call this; they hold no logic of their own (notepad rule 1)."""

import os
import uuid

from django.conf import settings
from django.db import transaction

from .models import File, Job
from .queue import enqueue_ingest
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
