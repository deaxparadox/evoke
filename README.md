# evoke

Evaluation-driven multimodal knowledge agent: upload PDFs, images and audio, ask questions, get answers that cite the exact page or timestamp, and measure every design choice.

| Folder | What it is |
| --- | --- |
| `ragling/` | Standalone, stateless library (uv project) |
| `backend/` | Django backend (uv project) |
| `streaming/` | Placeholder for the later FastAPI streaming service |

Each Python project is its own uv project. Run commands from inside the project folder, e.g. `cd ragling && uv sync --locked && uv run ruff check`.

Planning, specs and decisions live in the `evoke-track` repo.

## Start the stack (M0)

Needs Docker with Compose. Run from the repo root.

1. Copy the two env templates and fill in every value:
   ```
   cp .env.example .env
   cp backend/.env.example backend/.env
   ```
   - `.env` (Compose): `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET`. Optional: `POSTGRES_HOST_PORT` if 5432 is taken on your machine.
   - `backend/.env`: under Compose only `SECRET_KEY` and `ALLOWED_HOSTS` (for example `localhost,127.0.0.1`) are needed. The `POSTGRES_*` lines are used only when running the backend outside Compose, and Compose overrides them.
   - Nothing has a hidden default: a missing value stops the stack with a message naming it.
2. Start everything: `docker compose up -d --build`. Wait until `docker compose ps` shows all four services (`postgres`, `redis`, `seaweedfs`, `backend`) as healthy. The backend runs migrations on every start.
3. Create an admin user: `docker compose exec backend python manage.py createsuperuser`.
4. Open http://127.0.0.1:8000/admin/, log in, and add a user under Accounts > Users.
5. Stop and delete the data: `docker compose down -v`.

## Run the checks (per project)

```
cd ragling && uv sync --locked && uv run ruff check && uv run pytest
cd backend && uv sync --locked && uv run ruff check && uv run --env-file .env pytest   # needs a reachable Postgres
```

## Check that S3 (SeaweedFS) works

With the stack up, from the repo root (needs the root `.env` values exported):

```
set -a; . ./.env; set +a
uv run scripts/s3_smoke.py
```

It signs requests with your S3 keys, checks the bucket exists, then puts, reads back and deletes a small object. `boto3` is installed only in uv's temporary environment for this script, not in any project. It never creates the bucket. It is a manual check and does not run in CI.

## Browser test for the admin (Playwright)

Runs a real Chromium against the running stack. It is not part of the default `pytest` run or CI.

```
cd backend
uv sync --locked
uv run playwright install chromium        # downloads into backend/.playwright-browsers (project-local)
# with the stack up and an admin created (step 3 of "Start the stack"):
E2E_ADMIN_USER=admin E2E_ADMIN_PASSWORD='...' uv run --env-file .env pytest e2e
```

`E2E_ADMIN_USER` and `E2E_ADMIN_PASSWORD` are required; the test fails if they are missing. `E2E_BASE_URL` is optional (default `http://127.0.0.1:8000`). Each run leaves one new user in that stack's database; `docker compose down -v` clears it.
