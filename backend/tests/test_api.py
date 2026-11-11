import time

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from apps.documents import views
from apps.documents.models import File, Job, SegmentRow
from apps.documents.storage import InMemoryStorage

pytestmark = pytest.mark.django_db
PDF_BYTES = b"%PDF-1.4 fake"


@pytest.fixture
def storage(monkeypatch):
    s = InMemoryStorage()
    monkeypatch.setattr(views, "get_storage", lambda: s)
    return s


@pytest.fixture
def queued(monkeypatch):
    calls = []
    monkeypatch.setattr("apps.documents.services.enqueue_ingest", calls.append)
    return calls


@pytest.fixture
def alice():
    return get_user_model().objects.create_user("alice", password="pw-alice")


@pytest.fixture
def bob():
    return get_user_model().objects.create_user("bob", password="pw-bob")


def logged_in(user):
    c = APIClient()
    c.force_login(user)
    return c


def pdf(name="a.pdf", content=PDF_BYTES, content_type="application/pdf"):
    return SimpleUploadedFile(name, content, content_type=content_type)


# ---- login / logout / me ----

def test_login_needs_the_csrf_token_and_then_works():
    get_user_model().objects.create_user("carol", password="pw-carol")
    c = APIClient(enforce_csrf_checks=True)
    assert c.post("/api/auth/login", {"username": "carol", "password": "pw-carol"}).status_code == 403
    token = c.get("/api/auth/me").cookies["csrftoken"].value  # the cookie comes with the first call
    r = c.post("/api/auth/login", {"username": "carol", "password": "pw-carol"}, HTTP_X_CSRFTOKEN=token)
    assert r.status_code == 200 and r.json()["username"] == "carol"
    assert c.get("/api/auth/me").json()["username"] == "carol"


def test_wrong_password_is_401_and_does_not_log_in(alice):
    c = APIClient()
    assert c.post("/api/auth/login", {"username": "alice", "password": "nope"}).status_code == 401
    assert c.get("/api/auth/me").status_code == 403


def test_login_with_missing_fields_is_400():
    assert APIClient().post("/api/auth/login", {"username": "x"}).status_code == 400


def test_logout_ends_the_session(alice):
    c = logged_in(alice)
    assert c.post("/api/auth/logout").status_code == 204
    assert c.get("/api/auth/me").status_code == 403


def test_logged_in_writes_need_the_csrf_token(alice, storage, queued):
    c = APIClient(enforce_csrf_checks=True)
    c.force_login(alice)
    assert c.post("/api/files", {"file": pdf()}, format="multipart").status_code == 403


@pytest.mark.parametrize("method,url", [("get", "/api/files"), ("get", "/api/files/1"), ("get", "/api/files/1/pages"), ("post", "/api/files")])
def test_files_api_needs_login(method, url):
    assert getattr(APIClient(), method)(url).status_code == 403


# ---- upload ----

def test_upload_stores_the_file_and_queues_ingest_after_commit(alice, storage, queued, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        r = logged_in(alice).post("/api/files", {"file": pdf("Report.pdf")}, format="multipart")
    assert r.status_code == 201
    assert r.json()["name"] == "Report.pdf" and r.json()["status"] == "queued"
    file = File.objects.get()
    assert file.owner == alice and file.storage_key == f"users/{alice.pk}/files/{file.pk}/original"
    assert storage.get(file.storage_key) == PDF_BYTES
    job = Job.objects.get()
    assert (job.file, job.kind, job.status) == (file, "ingest", "queued")
    assert queued == []  # not yet: the transaction has not committed
    for cb in callbacks:
        cb()
    assert queued == [job.pk]


@pytest.mark.parametrize("name,ctype", [("a.txt", "application/pdf"), ("a.pdf", "text/plain"), ("a.png", "image/png")])
def test_only_pdfs_are_accepted(alice, storage, queued, name, ctype):
    r = logged_in(alice).post("/api/files", {"file": pdf(name, content_type=ctype)}, format="multipart")
    assert r.status_code == 415
    assert not File.objects.exists() and storage.objects == {}


def test_empty_file_and_missing_file_are_400(alice, storage, queued):
    c = logged_in(alice)
    assert c.post("/api/files", {"file": pdf(content=b"")}, format="multipart").status_code == 400
    assert c.post("/api/files", {}, format="multipart").status_code == 400


def test_the_size_cap_is_exact(alice, storage, queued, settings):
    settings.MAX_UPLOAD_BYTES = 100
    c = logged_in(alice)
    assert c.post("/api/files", {"file": pdf(content=b"x" * 100)}, format="multipart").status_code == 201
    r = c.post("/api/files", {"file": pdf("b.pdf", content=b"x" * 101)}, format="multipart")
    assert r.status_code == 413
    assert File.objects.count() == 1 and len(storage.objects) == 1


def test_default_cap_is_25_mb(settings):
    assert settings.MAX_UPLOAD_BYTES == 25 * 1024 * 1024


def test_a_storage_failure_leaves_no_file_row_and_no_job(alice, queued, monkeypatch):
    from botocore.exceptions import EndpointConnectionError

    class Broken:
        def put(self, key, data):
            raise EndpointConnectionError(endpoint_url="http://nowhere")

    monkeypatch.setattr(views, "get_storage", lambda: Broken())
    r = logged_in(alice).post("/api/files", {"file": pdf()}, format="multipart")
    assert r.status_code == 503
    assert not File.objects.exists() and not Job.objects.exists() and queued == []


def test_a_path_in_the_file_name_is_cut_off(alice, storage, queued):
    r = logged_in(alice).post("/api/files", {"file": pdf("../../etc/x.pdf")}, format="multipart")
    assert r.json()["name"] == "x.pdf"


def test_upload_handler_is_fast_with_storage_and_queue_faked(alice, storage, queued):
    c = logged_in(alice)
    c.get("/api/files")  # warm-up
    start = time.perf_counter()
    assert c.post("/api/files", {"file": pdf()}, format="multipart").status_code == 201
    assert time.perf_counter() - start < 0.2  # notepad budget: handlers answer in 200 ms


# ---- reading, owner only ----

def make_file(owner, name="a.pdf"):
    f = File.objects.create(owner=owner, name=name, media_type="application/pdf", size_bytes=1, storage_key=f"k/{owner.pk}/{name}")
    Job.objects.create(file=f, kind=Job.Kind.INGEST)
    return f


def test_list_shows_only_my_files_newest_first(alice, bob):
    a1, a2, b1 = make_file(alice, "1.pdf"), make_file(alice, "2.pdf"), make_file(bob, "b.pdf")
    rows = logged_in(alice).get("/api/files").json()
    assert [r["id"] for r in rows] == [a2.pk, a1.pk]
    assert b1.pk not in [r["id"] for r in rows]


def test_detail_and_pages_of_someone_elses_file_are_404_not_403(alice, bob):
    theirs = make_file(bob)
    c = logged_in(alice)
    assert c.get(f"/api/files/{theirs.pk}").status_code == 404
    assert c.get(f"/api/files/{theirs.pk}/pages").status_code == 404
    assert c.get("/api/files/999999").status_code == 404  # same answer as a real one that is not yours


def test_detail_shows_the_latest_job_status(alice):
    f = make_file(alice)
    Job.objects.create(file=f, kind=Job.Kind.RETRY_PAGE, status=Job.Status.RUNNING)
    assert logged_in(alice).get(f"/api/files/{f.pk}").json()["status"] == "running"


def test_pages_list_status_per_page_in_order(alice):
    f = make_file(alice)
    SegmentRow.objects.create(file=f, index=2, status="failed", error="boom", attempts=2)
    SegmentRow.objects.create(file=f, index=1, status="parsed", content_kind="text_layer")
    pages = logged_in(alice).get(f"/api/files/{f.pk}/pages").json()
    assert [(p["page"], p["status"], p["error"], p["attempts"]) for p in pages] == [(1, "parsed", "", 0), (2, "failed", "boom", 2)]


@pytest.mark.integration
def test_upload_through_the_api_lands_in_real_seaweedfs(alice, queued):
    """Needs the stack up (`pytest -m integration`). Uses the real S3 helper, cleans up after itself."""
    r = logged_in(alice).post("/api/files", {"file": pdf()}, format="multipart")
    assert r.status_code == 201
    key = File.objects.get().storage_key
    real = views.get_storage()
    try:
        assert real.get(key) == PDF_BYTES
    finally:
        real.delete(key)
    assert not real.exists(key)
