"""Storage abstraction: Firebase, S3/MinIO, or local. Selected via STORAGE_BACKEND env."""
from __future__ import annotations

import base64
import json
import mimetypes
import os
from abc import ABC, abstractmethod
from datetime import timedelta
from typing import Optional

from app.config import settings


class StorageBackend(ABC):
    @abstractmethod
    def put(self, data: bytes, key: str, content_type: Optional[str] = None) -> str:
        """Upload bytes to `key`. Returns the key."""

    @abstractmethod
    def get(self, key: str) -> bytes:
        """Read bytes from `key`."""

    @abstractmethod
    def signed_url(self, key: str, ttl_seconds: int = 900, method: str = "GET") -> str:
        """Generate a short-lived signed URL (default 15 min)."""

    @abstractmethod
    def delete(self, key: str) -> None:
        pass


class FirebaseStorage(StorageBackend):
    def __init__(self):
        import firebase_admin
        from firebase_admin import credentials, storage
        if not firebase_admin._apps:
            creds_json = json.loads(base64.b64decode(settings.firebase_credentials_b64))
            cred = credentials.Certificate(creds_json)
            firebase_admin.initialize_app(cred, {"storageBucket": settings.firebase_storage_bucket})
        self._storage = storage

    def _bucket(self):
        return self._storage.bucket()

    def put(self, data: bytes, key: str, content_type: Optional[str] = None) -> str:
        blob = self._bucket().blob(key)
        ct = content_type or mimetypes.guess_type(key)[0] or "application/octet-stream"
        blob.upload_from_string(data, content_type=ct)
        return key

    def get(self, key: str) -> bytes:
        return self._bucket().blob(key).download_as_bytes()

    def signed_url(self, key: str, ttl_seconds: int = 900, method: str = "GET") -> str:
        return self._bucket().blob(key).generate_signed_url(
            expiration=timedelta(seconds=ttl_seconds), method=method,
        )

    def delete(self, key: str) -> None:
        self._bucket().blob(key).delete()


class S3Storage(StorageBackend):
    """AWS S3 or MinIO (on-prem). Set S3_ENDPOINT_URL for MinIO."""
    def __init__(self):
        import boto3
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )
        if not settings.s3_bucket:
            raise RuntimeError("S3_BUCKET not set")
        self._bucket_name = settings.s3_bucket

    def put(self, data: bytes, key: str, content_type: Optional[str] = None) -> str:
        kwargs = {"Body": data, "Bucket": self._bucket_name, "Key": key}
        if content_type:
            kwargs["ContentType"] = content_type
        self._client.put_object(**kwargs)
        return key

    def get(self, key: str) -> bytes:
        return self._client.get_object(Bucket=self._bucket_name, Key=key)["Body"].read()

    def signed_url(self, key: str, ttl_seconds: int = 900, method: str = "GET") -> str:
        op = "get_object" if method.upper() == "GET" else "put_object"
        return self._client.generate_presigned_url(
            op, Params={"Bucket": self._bucket_name, "Key": key}, ExpiresIn=ttl_seconds,
        )

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket_name, Key=key)


class LocalStorage(StorageBackend):
    """Local-disk storage for dev. Signed URLs return file:// paths (override behind a proxy in prod)."""
    def __init__(self, root: str = "./storage"):
        self.root = root
        os.makedirs(root, exist_ok=True)

    def _path(self, key: str) -> str:
        path = os.path.join(self.root, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def put(self, data: bytes, key: str, content_type: Optional[str] = None) -> str:
        with open(self._path(key), "wb") as f:
            f.write(data)
        return key

    def get(self, key: str) -> bytes:
        with open(self._path(key), "rb") as f:
            return f.read()

    def signed_url(self, key: str, ttl_seconds: int = 900, method: str = "GET") -> str:
        return f"file://{os.path.abspath(self._path(key))}"

    def delete(self, key: str) -> None:
        path = self._path(key)
        if os.path.exists(path):
            os.remove(path)


_backend: StorageBackend | None = None


def get_storage() -> StorageBackend:
    global _backend
    if _backend is None:
        backend = settings.storage_backend.lower()
        if backend == "firebase":
            _backend = FirebaseStorage()
        elif backend == "s3":
            _backend = S3Storage()
        else:
            _backend = LocalStorage()
    return _backend
