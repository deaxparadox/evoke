from django.core.management import call_command
from django.test import Client


def test_admin_css_is_served_after_collectstatic(settings, tmp_path):
    settings.STATIC_ROOT = tmp_path
    call_command("collectstatic", interactive=False, verbosity=0)
    # A new Client builds a new handler, so WhiteNoise scans the freshly filled STATIC_ROOT.
    response = Client().get("/static/admin/css/base.css")
    assert response.status_code == 200
    assert "text/css" in response["Content-Type"]


def test_unknown_static_file_is_404():
    assert Client().get("/static/admin/css/does-not-exist.css").status_code == 404
