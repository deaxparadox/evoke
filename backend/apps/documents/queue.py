"""The seam between the API and the worker: sends a job to Celery. Called only after the database commit."""

import logging

from django.utils import timezone

from .models import Job

logger = logging.getLogger(__name__)


def _send(task, job_id: int, *args) -> None:
    try:
        task.delay(job_id, *args)
    except Exception as e:  # the broker is down: say so on the job instead of failing a request that already committed
        logger.exception("could not queue job %s", job_id)
        Job.objects.filter(pk=job_id).update(
            status=Job.Status.FAILED, error=f"could not queue: {type(e).__name__}: {e}"[:2000], finished_at=timezone.now()
        )


def enqueue_ingest(job_id: int) -> None:
    from .tasks import ingest_file

    _send(ingest_file, job_id)


def enqueue_retry(job_id: int, page: int, adapter: str | None) -> None:
    from .tasks import retry_page

    _send(retry_page, job_id, page, adapter)
