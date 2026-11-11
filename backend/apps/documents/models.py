"""Tables for ingestion. Every row is owned by a user, directly or through its file (ADR 0003)."""

from django.conf import settings
from django.db import models
from pgvector.django import HnswIndex, VectorField
from ragling.core.models import ContentKind, SegmentStatus

EMBEDDING_DIMENSIONS = 1024  # ADR 0003; a model with another size needs a new column and a re-embed


def _choices(enum):
    # Reuse ragling's enums so the database and the library cannot drift apart.
    return [(m.value, m.value) for m in enum]


class File(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="files")
    name = models.CharField(max_length=255)
    media_type = models.CharField(max_length=100)
    size_bytes = models.PositiveBigIntegerField()
    storage_key = models.CharField(max_length=500, unique=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Job(models.Model):
    class Kind(models.TextChoices):
        INGEST = "ingest"
        RETRY_PAGE = "retry_page"

    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        COMPLETED = "completed"
        COMPLETED_WITH_ERRORS = "completed_with_errors"
        FAILED = "failed"

    file = models.ForeignKey(File, on_delete=models.CASCADE, related_name="jobs")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.QUEUED)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # How many times a worker has picked this job up, and which page it was working on. Written before the work, so they
    # survive a worker that dies mid-page (BUG-20261111-2210).
    deliveries = models.PositiveIntegerField(default=0)
    current_page = models.PositiveIntegerField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["file", "status"])]


class SegmentRow(models.Model):
    """One page. Blocks are stored as JSON in the shape of `ragling.core.models.Block`."""

    file = models.ForeignKey(File, on_delete=models.CASCADE, related_name="segments")
    index = models.PositiveIntegerField()  # 1-based page number
    content_kind = models.CharField(max_length=20, choices=_choices(ContentKind), null=True, blank=True)
    status = models.CharField(max_length=20, choices=_choices(SegmentStatus), default=SegmentStatus.PENDING.value)
    error = models.TextField(blank=True)
    attempts = models.PositiveIntegerField(default=0)
    adapters_tried = models.JSONField(default=list, blank=True)
    blocks = models.JSONField(default=list, blank=True)
    width = models.FloatField(null=True, blank=True)
    height = models.FloatField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["file", "index"], name="one_segment_per_page"),
            models.CheckConstraint(condition=models.Q(index__gte=1), name="segment_index_from_1"),
        ]


class ChunkRow(models.Model):
    """A piece of a page plus its vector. `owner` is repeated here so a vector search needs no join."""

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="chunks")
    file = models.ForeignKey(File, on_delete=models.CASCADE, related_name="chunks")
    page = models.PositiveIntegerField()
    chunk_id = models.CharField(max_length=200)
    text = models.TextField()
    locations = models.JSONField(default=list)
    embedding_model = models.CharField(max_length=100)
    embedding = VectorField(dimensions=EMBEDDING_DIMENSIONS)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["file", "chunk_id"], name="one_chunk_id_per_file"),
        ]
        indexes = [
            models.Index(fields=["owner"]),
            models.Index(fields=["file", "page"]),
            HnswIndex(name="chunk_embedding_hnsw", fields=["embedding"], m=16, ef_construction=64, opclasses=["vector_cosine_ops"]),
        ]
