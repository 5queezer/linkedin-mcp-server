"""Google Cloud Storage backend for auth-state persistence."""

import logging
from pathlib import Path

from google.cloud import storage  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)


class GCSBackend:
    """Persist auth artifacts to a GCS bucket."""

    def __init__(self, bucket: str, prefix: str = ""):
        self.bucket = bucket
        self.prefix = prefix
        self._client = storage.Client()
        self._bucket = self._client.bucket(bucket)

    def download(self, remote_key: str, local_path: Path) -> bool:
        try:
            blob = self._bucket.blob(remote_key)
            if not blob.exists():
                logger.warning(
                    "GCS object not found: gs://%s/%s", self.bucket, remote_key
                )
                return False
            local_path.parent.mkdir(parents=True, exist_ok=True)
            blob.download_to_filename(str(local_path))
            logger.debug(
                "Downloaded gs://%s/%s → %s", self.bucket, remote_key, local_path
            )
            return True
        except Exception:
            logger.warning(
                "GCS download failed: gs://%s/%s",
                self.bucket,
                remote_key,
                exc_info=True,
            )
            return False

    def upload(self, local_path: Path, remote_key: str) -> bool:
        try:
            blob = self._bucket.blob(remote_key)
            blob.upload_from_filename(str(local_path))
            logger.debug(
                "Uploaded %s → gs://%s/%s", local_path, self.bucket, remote_key
            )
            return True
        except Exception:
            logger.warning(
                "GCS upload failed: %s → gs://%s/%s",
                local_path,
                self.bucket,
                remote_key,
                exc_info=True,
            )
            return False

    def delete(self, remote_key: str) -> bool:
        try:
            blob = self._bucket.blob(remote_key)
            if blob.exists():
                blob.delete()
                logger.debug("Deleted gs://%s/%s", self.bucket, remote_key)
            return True
        except Exception:
            logger.warning(
                "GCS delete failed: gs://%s/%s", self.bucket, remote_key, exc_info=True
            )
            return False
