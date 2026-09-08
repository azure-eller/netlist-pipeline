"""Object store: S3 API; MinIO locally when S3_ENDPOINT is set."""

import hashlib
from functools import cache
from typing import Any

import boto3
from botocore.exceptions import ClientError

from pipeline.config import settings


@cache
def _client() -> Any:
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
    )


def ensure_bucket() -> None:
    """Create the bucket on MinIO. Real S3 buckets come from infra/aws.yaml."""
    if not settings.s3_endpoint:
        return
    try:
        _client().head_bucket(Bucket=settings.s3_bucket)
    except ClientError:
        _client().create_bucket(Bucket=settings.s3_bucket)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def put(key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    _client().put_object(Bucket=settings.s3_bucket, Key=key, Body=data, ContentType=content_type)
    return key


def get(key: str) -> bytes:
    return _client().get_object(Bucket=settings.s3_bucket, Key=key)["Body"].read()  # type: ignore[no-any-return]


def presign(key: str, seconds: int = 900) -> str:
    return _client().generate_presigned_url(  # type: ignore[no-any-return]
        "get_object", Params={"Bucket": settings.s3_bucket, "Key": key}, ExpiresIn=seconds
    )


def ping() -> None:
    _client().head_bucket(Bucket=settings.s3_bucket)
