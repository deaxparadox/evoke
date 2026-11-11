import os

from celery import Celery
from celery.signals import worker_init

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("config")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@worker_init.connect
def check_worker_config(**_):
    # Fail at boot, not on the first job: the worker needs the pinned model revision (ADR 0004).
    from apps.documents.wiring import check_worker_settings

    check_worker_settings()
