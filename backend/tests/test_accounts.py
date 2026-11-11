import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model

BACKEND = Path(__file__).resolve().parent.parent
REQUIRED = (
    "SECRET_KEY", "ALLOWED_HOSTS",
    "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD",
    "S3_ENDPOINT", "S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_BUCKET",
)


def import_settings(env):
    return subprocess.run(
        [sys.executable, "-c", "import config.settings"],
        cwd=BACKEND, env=env, capture_output=True, text=True, check=False,
    )


def test_missing_required_settings_fail_and_are_named():
    env = {k: v for k, v in os.environ.items() if k not in REQUIRED}
    result = import_settings(env)
    assert result.returncode != 0
    for name in REQUIRED:
        assert name in result.stderr


def test_single_missing_value_is_named():
    env = {**os.environ, "SECRET_KEY": ""}
    result = import_settings(env)
    assert result.returncode != 0
    assert "SECRET_KEY" in result.stderr
    assert "POSTGRES_HOST" not in result.stderr


def test_user_model_is_the_custom_one():
    assert settings.AUTH_USER_MODEL == "accounts.User"
    assert get_user_model() is apps.get_model("accounts", "User")


@pytest.mark.django_db
def test_superuser_can_be_created():
    user = get_user_model().objects.create_superuser("root", password="pw-123456")
    assert user.is_staff and user.is_superuser


@pytest.mark.django_db
def test_admin_login_page_and_user_admin_registered(client, admin_client):
    assert client.get("/admin/login/").status_code == 200
    assert admin_client.get("/admin/accounts/user/").status_code == 200
