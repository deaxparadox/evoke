"""Local `voyage-4-nano` embedder (ADR 0004). Needs the extra: `ragling[embed-local]`."""

import math
from collections.abc import Sequence
from typing import Any

MODEL_REPO = "voyageai/voyage-4-nano"
DIMENSIONS = 1024  # Matryoshka truncation of the model's 2048 (ADR 0003)


def _load_model(revision: str) -> Any:
    # Imported here so that importing ragling never pulls in torch.
    from sentence_transformers import SentenceTransformer

    # trust_remote_code runs the repo's own modelling file. It is reviewed once per
    # revision (see the FEAT-20261103-1300 spec log), which is why `revision` is required.
    return SentenceTransformer(MODEL_REPO, revision=revision, trust_remote_code=True, truncate_dim=DIMENSIONS)


def _unit(vector: Sequence[float]) -> list[float]:
    # The model normalises its full 2048-dim output; cutting it to 1024 breaks that, and the
    # store uses cosine distance, so we normalise again after the cut.
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0:
        raise ValueError("model returned a zero vector")
    return [float(x) / norm for x in vector]


class VoyageLocalEmbedder:
    model_name = "voyage-4-nano"
    dimensions = DIMENSIONS

    def __init__(self, revision: str) -> None:
        if not revision.strip():
            raise ValueError("revision is required: pin the model to a commit hash")
        self._model = _load_model(revision)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        return [_unit(v) for v in self._model.encode_document(list(texts))]

    def embed_query(self, text: str) -> list[float]:
        return _unit(self._model.encode_query(text))
