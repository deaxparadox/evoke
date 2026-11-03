# /// script
# requires-python = ">=3.13"
# dependencies = ["boto3==1.43.123"]
# ///
"""Check that SeaweedFS accepts our S3 keys and has the bucket. Manual check, not part of CI.

Run from the repo root with the stack up and the Compose .env values exported:
    set -a; . ./.env; set +a
    uv run scripts/s3_smoke.py

boto3 lives only in uv's temporary environment for this script; it is not a project dependency.
Required env: S3_ACCESS_KEY, S3_SECRET_KEY, S3_BUCKET. Optional: S3_ENDPOINT (default http://127.0.0.1:8333).
Does not create the bucket: a missing bucket means the Compose config is wrong, and creating it would hide that.
"""

import os
import sys
import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

REQUIRED = ("S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_BUCKET")


def main() -> int:
    missing = [n for n in REQUIRED if not os.environ.get(n)]
    if missing:
        print(f"FAIL: missing required environment variables: {', '.join(missing)}")
        return 2
    endpoint = os.environ.get("S3_ENDPOINT", "http://127.0.0.1:8333")
    bucket = os.environ["S3_BUCKET"]
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.environ["S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["S3_SECRET_KEY"],
        region_name="us-east-1",  # SeaweedFS does not care; boto3 needs one to sign requests
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}, retries={"max_attempts": 1}),
    )
    key = f"smoke/{uuid.uuid4()}.txt"
    body = uuid.uuid4().bytes
    try:
        s3.head_bucket(Bucket=bucket)
        print(f"ok   bucket '{bucket}' exists (signed request accepted)")
        s3.put_object(Bucket=bucket, Key=key, Body=body)
        print(f"ok   put {key}")
        got = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        if got != body:
            print("FAIL: object read back differs from what was written")
            return 1
        print("ok   get returned the same bytes")
        s3.delete_object(Bucket=bucket, Key=key)
        try:
            s3.head_object(Bucket=bucket, Key=key)
        except ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                print("ok   delete removed the object")
                return 0
            raise
        print("FAIL: object still exists after delete")
        return 1
    except ClientError as e:
        code = e.response["Error"]["Code"]
        hint = {
            "404": "bucket missing (check S3_BUCKET against the Compose .env)",
            "NoSuchBucket": "bucket missing (check S3_BUCKET against the Compose .env)",
            "403": "keys rejected (check S3_ACCESS_KEY / S3_SECRET_KEY)",
            "InvalidAccessKeyId": "keys rejected (check S3_ACCESS_KEY)",
            "SignatureDoesNotMatch": "keys rejected (check S3_SECRET_KEY)",
        }.get(code, "unexpected S3 error")
        print(f"FAIL: {code}: {hint}")
        return 1
    except BotoCoreError as e:
        print(f"FAIL: could not reach {endpoint}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
