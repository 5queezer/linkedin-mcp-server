from pathlib import Path

import pytest

from linkedin_mcp_server.config.schema import StorageConfig


class TestLocalBackend:
    def test_download_returns_true(self, tmp_path):
        from linkedin_mcp_server.storage.backend import LocalBackend

        backend = LocalBackend()
        assert backend.download("key", tmp_path / "file.json") is True

    def test_upload_returns_true(self, tmp_path):
        from linkedin_mcp_server.storage.backend import LocalBackend

        backend = LocalBackend()
        assert backend.upload(tmp_path / "file.json", "key") is True

    def test_delete_returns_true(self):
        from linkedin_mcp_server.storage.backend import LocalBackend

        backend = LocalBackend()
        assert backend.delete("key") is True


class TestGetStorageBackend:
    def test_returns_local_by_default(self):
        from linkedin_mcp_server.storage.backend import (
            LocalBackend,
            get_storage_backend,
        )

        config = StorageConfig()
        backend = get_storage_backend(config)
        assert isinstance(backend, LocalBackend)

    def test_gcs_raises_without_dependency(self, monkeypatch):
        from linkedin_mcp_server.storage import backend as backend_module

        monkeypatch.setattr(backend_module, "_import_gcs_backend", _fake_import_error)
        from linkedin_mcp_server.storage.backend import get_storage_backend

        config = StorageConfig(backend="gcs", gcs_bucket="b", username="u")
        with pytest.raises(ImportError, match="linkedin-scraper-mcp\\[gcs\\]"):
            get_storage_backend(config)


def _fake_import_error():
    raise ImportError("No module named 'google.cloud'")


class InMemoryBackend:
    """Test double that stores data in a dict."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.prefix = ""

    def download(self, remote_key: str, local_path: Path) -> bool:
        if remote_key not in self.objects:
            return False
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(self.objects[remote_key])
        return True

    def upload(self, local_path: Path, remote_key: str) -> bool:
        if not local_path.exists():
            return False
        self.objects[remote_key] = local_path.read_bytes()
        return True

    def delete(self, remote_key: str) -> bool:
        self.objects.pop(remote_key, None)
        return True


class FailingBackend:
    """Test double that always fails."""

    prefix = ""

    def download(self, remote_key: str, local_path: Path) -> bool:
        return False

    def upload(self, local_path: Path, remote_key: str) -> bool:
        return False

    def delete(self, remote_key: str) -> bool:
        return False


class TestSyncFromRemote:
    def test_downloads_both_files(self, tmp_path):
        from linkedin_mcp_server.storage.backend import sync_from_remote

        backend = InMemoryBackend()
        backend.objects["testuser/cookies.json"] = b'{"cookies": []}'
        backend.objects["testuser/source-state.json"] = b'{"version": 1}'
        sync_from_remote(tmp_path, "testuser", backend)
        assert (tmp_path / "cookies.json").read_bytes() == b'{"cookies": []}'
        assert (tmp_path / "source-state.json").read_bytes() == b'{"version": 1}'

    def test_raises_on_download_failure(self, tmp_path):
        from linkedin_mcp_server.storage.backend import (
            StorageSyncError,
            sync_from_remote,
        )

        backend = FailingBackend()
        with pytest.raises(StorageSyncError, match="cookies.json"):
            sync_from_remote(tmp_path, "testuser", backend)

    def test_creates_auth_root_if_missing(self, tmp_path):
        from linkedin_mcp_server.storage.backend import sync_from_remote

        auth_root = tmp_path / "nonexistent" / "auth"
        backend = InMemoryBackend()
        backend.objects["u/cookies.json"] = b"{}"
        backend.objects["u/source-state.json"] = b"{}"
        sync_from_remote(auth_root, "u", backend)
        assert auth_root.is_dir()


class TestSyncToRemote:
    def test_uploads_both_files(self, tmp_path):
        from linkedin_mcp_server.storage.backend import sync_to_remote

        (tmp_path / "cookies.json").write_text('{"c": 1}')
        (tmp_path / "source-state.json").write_text('{"s": 1}')
        backend = InMemoryBackend()
        result = sync_to_remote(tmp_path, "testuser", backend)
        assert result is True
        assert backend.objects["testuser/cookies.json"] == b'{"c": 1}'
        assert backend.objects["testuser/source-state.json"] == b'{"s": 1}'

    def test_skips_missing_files(self, tmp_path):
        from linkedin_mcp_server.storage.backend import sync_to_remote

        backend = InMemoryBackend()
        result = sync_to_remote(tmp_path, "testuser", backend)
        assert result is True
        assert len(backend.objects) == 0

    def test_returns_false_on_failure(self, tmp_path):
        from linkedin_mcp_server.storage.backend import sync_to_remote

        (tmp_path / "cookies.json").write_text("{}")
        backend = FailingBackend()
        result = sync_to_remote(tmp_path, "testuser", backend)
        assert result is False


class TestDeleteRemote:
    def test_deletes_both_keys(self):
        from linkedin_mcp_server.storage.backend import delete_remote

        backend = InMemoryBackend()
        backend.objects["testuser/cookies.json"] = b"{}"
        backend.objects["testuser/source-state.json"] = b"{}"
        result = delete_remote("testuser", backend)
        assert result is True
        assert len(backend.objects) == 0


class TestGCSBackendImport:
    def test_gcs_backend_has_required_methods(self):
        """Verify GCSBackend satisfies the StorageBackend protocol."""
        try:
            from linkedin_mcp_server.storage.gcs import GCSBackend
        except ImportError:
            pytest.skip("google-cloud-storage not installed")

        from linkedin_mcp_server.storage.backend import StorageBackend

        backend = GCSBackend(bucket="test", prefix="pfx")
        assert isinstance(backend, StorageBackend)

    def test_gcs_backend_stores_config(self):
        try:
            from linkedin_mcp_server.storage.gcs import GCSBackend
        except ImportError:
            pytest.skip("google-cloud-storage not installed")

        backend = GCSBackend(bucket="my-bucket", prefix="my-prefix")
        assert backend.bucket == "my-bucket"
        assert backend.prefix == "my-prefix"
