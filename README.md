# evoke

Evaluation-driven multimodal knowledge agent: upload PDFs, images and audio, ask questions, get answers that cite the exact page or timestamp, and measure every design choice.

| Folder | What it is |
| --- | --- |
| `ragling/` | Standalone, stateless library (uv project) |
| `backend/` | Django backend (uv project) |
| `streaming/` | Placeholder for the later FastAPI streaming service |

Each Python project is its own uv project. Run commands from inside the project folder, e.g. `cd ragling && uv sync --locked && uv run ruff check`.

Planning, specs and decisions live in the `evoke-track` repo.
