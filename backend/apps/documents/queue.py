"""The seam between the API and the worker. FEAT-20261103-1304 replaces the body with the Celery task call."""

import logging

logger = logging.getLogger(__name__)


def enqueue_ingest(job_id: int) -> None:
    # Named, visible fallback: until the worker exists the job stays `queued`.
    logger.warning("ingest queue is not connected yet (FEAT-20261103-1304); job %s stays queued", job_id)
