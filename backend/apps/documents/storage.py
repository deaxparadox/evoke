"""Object storage in SeaweedFS over S3. Same client settings as scripts/s3_smoke.py (path-style, v4 signing)."""

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from django.conf import settings

_NOT_FOUND = ("404", "NoSuchKey", "NotFound")


def storage_key(user_id: int, file_id: int) -> str:
    return f"users/{user_id}/files/{file_id}/original"


class S3Storage:
    def __init__(self, endpoint: str, access_key: str, secret_key: str, bucket: str) -> None:
        self.bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="us-east-1",  # SeaweedFS ignores it; boto3 needs one to sign
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    @classmethod
    def from_settings(cls) -> "S3Storage":
        return cls(settings.S3_ENDPOINT, settings.S3_ACCESS_KEY, settings.S3_SECRET_KEY, settings.S3_BUCKET)

    def put(self, key: str, data: bytes) -> None:
        self._client.put_object(Bucket=self.bucket, Key=key, Body=data)

    def get(self, key: str) -> bytes:
        try:
            return self._client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except ClientError as e:
            if e.response["Error"]["Code"] in _NOT_FOUND:
                raise FileNotFoundError(key) from e
            raise

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)  # S3 deletes are idempotent: a missing key is fine

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as e:
            if e.response["Error"]["Code"] in _NOT_FOUND:
                return False
            raise  # 403, 5xx etc. must not look like "not there"
        return True


class InMemoryStorage:
    """Same interface, for tests that are not about storage."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, key: str, data: bytes) -> None:
        self.objects[key] = data

    def get(self, key: str) -> bytes:
        try:
            return self.objects[key]
        except KeyError:
            raise FileNotFoundError(key) from None

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def exists(self, key: str) -> bool:
        return key in self.objects
