import os
from pathlib import Path

import pytest

# Keep the downloaded browsers inside the project, not in ~/.cache (no global installs).
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(Path(__file__).resolve().parent.parent / ".playwright-browsers"))


@pytest.fixture(scope="session")
def base_url() -> str:
    # Behavior knob with a safe default: the Compose stack on this machine.
    return os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


@pytest.fixture(scope="session")
def admin_credentials() -> tuple[str, str]:
    # Required, no defaults: fail loudly instead of guessing a login.
    missing = [n for n in ("E2E_ADMIN_USER", "E2E_ADMIN_PASSWORD") if not os.environ.get(n)]
    if missing:
        pytest.fail(f"Missing required environment variables: {', '.join(missing)}", pytrace=False)
    return os.environ["E2E_ADMIN_USER"], os.environ["E2E_ADMIN_PASSWORD"]
