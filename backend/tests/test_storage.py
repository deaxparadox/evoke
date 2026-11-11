
import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from apps.documents.storage import InMemoryStorage, S3Storage, storage_key


class StorageContract:
    def make_storage(self): raise NotImplementedError

    def test_put_then_get_returns_the_same_bytes(self):
        s = self.make_storage()
        s.put("t/a", b"\x00\x01abc")
        assert s.get("t/a") == b"\x00\x01abc"

    def test_exists_follows_put_and_delete(self):
        s = self.make_storage()
        assert not s.exists("t/b")
        s.put("t/b", b"x")
        assert s.exists("t/b")
        s.delete("t/b")
        assert not s.exists("t/b")

    def test_get_of_a_missing_key_raises_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            self.make_storage().get("t/missing")

    def test_deleting_a_missing_key_is_fine(self):
        self.make_storage().delete("t/never-existed")

    def test_put_replaces(self):
        s = self.make_storage()
        s.put("t/c", b"one")
        s.put("t/c", b"two")
        assert s.get("t/c") == b"two"


class TestInMemoryStorage(StorageContract):
    def make_storage(self): return InMemoryStorage()


@pytest.mark.integration
class TestRealSeaweedFS(StorageContract):
    """Needs the stack up and S3_* in the environment. Leaves nothing behind."""

    def make_storage(self):
        from django.conf import settings
        return S3Storage(settings.S3_ENDPOINT, settings.S3_ACCESS_KEY, settings.S3_SECRET_KEY, settings.S3_BUCKET)

    @pytest.fixture(autouse=True)
    def _unique_prefix_cleanup(self):
        yield
        s = self.make_storage()
        for k in ("t/a", "t/b", "t/c"):
            s.delete(k)


def test_storage_key_layout():
    assert storage_key(7, 42) == "users/7/files/42/original"


@pytest.fixture
def stubbed():
    s = S3Storage("http://127.0.0.1:1", "k", "s", "bucket")
    with Stubber(s._client) as stub:
        yield s, stub


def client_error(code, status):
    return {"service_error_code": code, "http_status_code": status}


def test_exists_is_false_on_404(stubbed):
    s, stub = stubbed
    stub.add_client_error("head_object", **client_error("404", 404))
    assert s.exists("k") is False


def test_exists_does_not_hide_access_errors(stubbed):
    s, stub = stubbed
    stub.add_client_error("head_object", **client_error("403", 403))
    with pytest.raises(ClientError):
        s.exists("k")


def test_get_maps_no_such_key_only(stubbed):
    s, stub = stubbed
    stub.add_client_error("get_object", **client_error("NoSuchKey", 404))
    with pytest.raises(FileNotFoundError):
        s.get("k")
    stub.add_client_error("get_object", **client_error("AccessDenied", 403))
    with pytest.raises(ClientError):
        s.get("k")


def test_put_errors_are_not_swallowed(stubbed):
    s, stub = stubbed
    stub.add_client_error("put_object", **client_error("InternalError", 500))
    with pytest.raises(ClientError):
        s.put("k", b"x")


def test_from_settings_uses_the_required_settings(settings):
    settings.S3_ENDPOINT, settings.S3_ACCESS_KEY, settings.S3_SECRET_KEY, settings.S3_BUCKET = "http://h:1", "a", "b", "bk"
    s = S3Storage.from_settings()
    assert s.bucket == "bk" and s._client.meta.endpoint_url == "http://h:1"
