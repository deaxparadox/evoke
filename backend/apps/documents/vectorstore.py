"""`VectorStore` port over Postgres + pgvector (ADR 0003). Owner and file ids are the database primary keys as text."""

from collections.abc import Sequence

from django.db import connection, transaction
from pgvector.django import CosineDistance
from ragling.core.models import Chunk, Location
from ragling.ports import Hit

from .models import ChunkRow, File


class PgVectorStore:
    def upsert(self, owner_id: str, items: Sequence[tuple[Chunk, Sequence[float]]], model_name: str) -> None:
        if not items:
            return
        placed = [(chunk, vector, chunk.locations[0]) for chunk, vector in items]
        file_ids = {int(loc.file_id) for _, _, loc in placed}
        # The owner column and the file's owner must agree, or a row could end up under the wrong user.
        owned = set(File.objects.filter(pk__in=file_ids, owner_id=int(owner_id)).values_list("pk", flat=True))
        if file_ids - owned:
            raise ValueError(f"files {sorted(file_ids - owned)} do not exist or do not belong to owner {owner_id}")
        rows = [
            ChunkRow(
                owner_id=int(owner_id), file_id=int(loc.file_id), page=loc.page, chunk_id=chunk.id, text=chunk.text,
                locations=[loc.model_dump(mode="json") for loc in chunk.locations],
                embedding_model=model_name, embedding=list(vector),
            )
            for chunk, vector, loc in placed
        ]
        ChunkRow.objects.bulk_create(
            rows, update_conflicts=True, unique_fields=["file", "chunk_id"],
            update_fields=["page", "text", "locations", "embedding_model", "embedding"],
        )

    def search(self, owner_id: str, vector: Sequence[float], model_name: str, k: int) -> list[Hit]:
        with transaction.atomic():  # SET LOCAL lasts until the end of this transaction only
            with connection.cursor() as cursor:
                # Without this, the owner filter is applied after the index scan and can leave fewer than k rows.
                cursor.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
            rows = list(
                ChunkRow.objects.filter(owner_id=int(owner_id), embedding_model=model_name)
                .annotate(distance=CosineDistance("embedding", list(vector)))
                .order_by("distance")[:k]
            )
        # relaxed_order can return rows slightly out of order; k rows are cheap to sort again.
        rows.sort(key=lambda r: r.distance)
        return [
            Hit(chunk_id=r.chunk_id, text=r.text, locations=[Location(**x) for x in r.locations], score=1 - r.distance)
            for r in rows
        ]

    def delete_page(self, owner_id: str, file_id: str, page: int) -> None:
        ChunkRow.objects.filter(owner_id=int(owner_id), file_id=int(file_id), page=page).delete()

    def delete_file(self, owner_id: str, file_id: str) -> None:
        ChunkRow.objects.filter(owner_id=int(owner_id), file_id=int(file_id)).delete()
