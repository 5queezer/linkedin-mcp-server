"""Storage backend protocol and local no-op implementation."""

import logging
from pathlib import Path
from typing import Protocol, runtime_checkable

from linkedin_mcp_server.config.schema import StorageConfig

logger = logging.getLogger(__name__)


class StorageSyncError(Exception):
    """Raised when a required storage sync operation fails."""


@runtime_checkable
class StorageBackend(Protocol):
    def download(self, remote_key: str, local_path: Path) -> bool: ...
    def upload(self, local_path: Path, remote_key: str) -> bool: ...
    def delete(self, remote_key: str) -> bool: ...


class LocalBackend:
    """No-op backend for local-only operation."""

    def download(self, remote_key: str, local_path: Path) -> bool:
        return True

    def upload(self, local_path: Path, remote_key: str) -> bool:
        return True

    def delete(self, remote_key: str) -> bool:
        return True


def _import_gcs_backend():
    from linkedin_mcp_server.storage.gcs import GCSBackend  # type: ignore[import]

    return GCSBackend


def get_storage_backend(config: StorageConfig) -> StorageBackend:
    """Create a storage backend from configuration."""
    if config.backend == "gcs":
        try:
            GCSBackend = _import_gcs_backend()
        except ImportError:
            raise ImportError(
                "GCS storage backend requires google-cloud-storage. "
                "Install with: pip install linkedin-scraper-mcp[gcs]"
            )
        return GCSBackend(bucket=config.gcs_bucket, prefix=config.gcs_prefix)
    return LocalBackend()


def _remote_key(prefix: str, username: str, filename: str) -> str:
    """Build the GCS object key."""
    parts = [p for p in (prefix, username, filename) if p]
    return "/".join(parts)


def sync_from_remote(auth_root: Path, username: str, backend: StorageBackend) -> bool:
    """Download auth artifacts from remote storage. Raises on failure."""
    auth_root.mkdir(parents=True, exist_ok=True)
    prefix = str(getattr(backend, "prefix", ""))

    for filename in ("cookies.json", "source-state.json"):
        key = _remote_key(prefix, username, filename)
        local = auth_root / filename
        if not backend.download(key, local):
            raise StorageSyncError(
                f"Failed to download {filename} from remote storage. "
                f"Key: {key}. Ensure the bucket exists and contains auth artifacts. "
                f"Run --login on a machine with browser access to create them."
            )
    logger.info("Auth state restored from remote storage for user %s", username)
    return True


def sync_to_remote(auth_root: Path, username: str, backend: StorageBackend) -> bool:
    """Upload auth artifacts to remote storage. Best-effort (logs, doesn't raise)."""
    prefix = str(getattr(backend, "prefix", ""))

    for filename in ("cookies.json", "source-state.json"):
        local = auth_root / filename
        if not local.exists():
            logger.debug("Skipping upload of %s (not found locally)", filename)
            continue
        key = _remote_key(prefix, username, filename)
        if not backend.upload(local, key):
            logger.warning("Failed to upload %s to remote storage", filename)
            return False
    logger.info("Auth state synced to remote storage for user %s", username)
    return True


def delete_remote(username: str, backend: StorageBackend) -> bool:
    """Delete auth artifacts from remote storage. Best-effort."""
    prefix = str(getattr(backend, "prefix", ""))

    success = True
    for filename in ("cookies.json", "source-state.json"):
        key = _remote_key(prefix, username, filename)
        if not backend.delete(key):
            logger.warning("Failed to delete %s from remote storage", filename)
            success = False
    return success
