"""Chooses the concrete adapters from settings. The one place that knows which parser, embedder, store and storage run."""

from functools import lru_cache

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .storage import S3Storage
from .vectorstore import PgVectorStore

PARSERS = ("pdfplumber", "pypdfium2")


def get_storage():
    return S3Storage.from_settings()


def get_store():
    return PgVectorStore()


def get_parser(name: str | None = None):
    name = name or settings.PDF_PARSER
    # Imported here so a process without the parsing extra can still import this module (the web image).
    if name == "pdfplumber":
        from ragling.formats.pdf.pdfplumber_parser import PdfplumberParser

        return PdfplumberParser()
    if name == "pypdfium2":
        from ragling.formats.pdf.pypdfium2_parser import Pypdfium2Parser

        return Pypdfium2Parser()
    raise ImproperlyConfigured(f"unknown PDF parser {name!r}; choose one of {PARSERS}")


@lru_cache(maxsize=1)
def get_embedder():
    """Loaded once per worker process; the first call is slow (model load)."""
    from ragling.providers.embed.voyage_local import VoyageLocalEmbedder

    return VoyageLocalEmbedder(revision=settings.EMBED_MODEL_REVISION)


def check_worker_settings() -> None:
    if not settings.EMBED_MODEL_REVISION.strip():
        raise ImproperlyConfigured("EMBED_MODEL_REVISION is required for the worker: pin the model to a commit hash")
    get_parser()  # raises on an unknown PDF_PARSER name
