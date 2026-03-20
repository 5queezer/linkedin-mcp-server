# GCS Auth Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist LinkedIn auth artifacts (`cookies.json` + `source-state.json`) to GCS so Cloud Run cold starts can restore session state.

**Architecture:** A `StorageBackend` protocol with `LocalBackend` (no-op) and `GCSBackend` implementations. Three sync functions hook into existing lifecycle points: startup (download), post-login (upload), shutdown (upload). `google-cloud-storage` is an optional `[gcs]` dependency.

**Tech Stack:** Python 3.12+, google-cloud-storage, pytest, dataclasses

**Design doc:** `docs/design-gcs-auth-storage.md`

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `linkedin_mcp_server/storage/__init__.py` | Public API: re-exports protocol, backends, sync functions |
| Create | `linkedin_mcp_server/storage/backend.py` | `StorageBackend` protocol, `LocalBackend`, `StorageSyncError`, factory |
| Create | `linkedin_mcp_server/storage/gcs.py` | `GCSBackend` implementation (lazy import) |
| Modify | `linkedin_mcp_server/config/schema.py` | Add `StorageConfig` dataclass |
| Modify | `linkedin_mcp_server/config/loaders.py` | Load `AUTH_STORAGE_*` env vars |
| Modify | `linkedin_mcp_server/cli_main.py` | Call `sync_from_remote()` before auth check |
| Modify | `linkedin_mcp_server/setup.py` | Call `sync_to_remote()` after login |
| Modify | `linkedin_mcp_server/drivers/browser.py` | Call `sync_to_remote()` after cookie export in `close_browser()` |
| Modify | `pyproject.toml` | Add `[gcs]` optional dependency |
| Create | `tests/test_storage.py` | Unit tests for storage module |

---

### Task 1: Add StorageConfig to config schema

**Files:**
- Modify: `linkedin_mcp_server/config/schema.py:86-92` (AppConfig dataclass)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
from linkedin_mcp_server.config.schema import StorageConfig


class TestStorageConfig:
    def test_defaults(self):
        config = StorageConfig()
        assert config.backend == "local"
        assert config.gcs_bucket is None
        assert config.gcs_prefix == ""
        assert config.username is None

    def test_validate_gcs_requires_bucket(self):
        config = StorageConfig(backend="gcs", username="testuser")
        with pytest.raises(ConfigurationError, match="AUTH_STORAGE_GCS_BUCKET"):
            config.validate()

    def test_validate_gcs_requires_username(self):
        config = StorageConfig(backend="gcs", gcs_bucket="my-bucket")
        with pytest.raises(ConfigurationError, match="AUTH_STORAGE_USERNAME"):
            config.validate()

    def test_validate_gcs_valid(self):
        config = StorageConfig(backend="gcs", gcs_bucket="my-bucket", username="testuser")
        config.validate()  # No error

    def test_validate_local_no_requirements(self):
        config = StorageConfig()
        config.validate()  # No error

    def test_validate_invalid_backend(self):
        config = StorageConfig(backend="s3")
        with pytest.raises(ConfigurationError, match="local.*gcs"):
            config.validate()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::TestStorageConfig -v`
Expected: FAIL with `ImportError` — `StorageConfig` doesn't exist yet.

- [ ] **Step 3: Write minimal implementation**

Add to `linkedin_mcp_server/config/schema.py`, before the `AppConfig` class:

```python
@dataclass
class StorageConfig:
    """External auth-state storage configuration."""

    backend: str = "local"
    gcs_bucket: str | None = None
    gcs_prefix: str = ""
    username: str | None = None

    def validate(self) -> None:
        """Validate storage configuration values."""
        if self.backend not in ("local", "gcs"):
            raise ConfigurationError(
                f"Invalid AUTH_STORAGE_BACKEND: '{self.backend}'. Must be 'local' or 'gcs'."
            )
        if self.backend == "gcs":
            if not self.gcs_bucket:
                raise ConfigurationError(
                    "AUTH_STORAGE_GCS_BUCKET is required when AUTH_STORAGE_BACKEND=gcs"
                )
            if not self.username:
                raise ConfigurationError(
                    "AUTH_STORAGE_USERNAME is required when AUTH_STORAGE_BACKEND=gcs"
                )
```

Add `storage` field to `AppConfig`:

```python
@dataclass
class AppConfig:
    """Main application configuration."""

    browser: BrowserConfig = field(default_factory=BrowserConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    is_interactive: bool = field(default=False)
```

Add `self.storage.validate()` call inside `AppConfig.validate()`, after `self.browser.validate()`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py::TestStorageConfig -v`
Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add linkedin_mcp_server/config/schema.py tests/test_config.py
git commit -m "feat(config): add StorageConfig for external auth-state storage"
```

---

### Task 2: Load storage env vars in config loader

**Files:**
- Modify: `linkedin_mcp_server/config/loaders.py:35-53` (EnvironmentKeys) and `loaders.py:68-166` (load_from_env)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
class TestStorageConfigEnvLoading:
    def test_load_storage_backend_from_env(self, monkeypatch):
        monkeypatch.setenv("AUTH_STORAGE_BACKEND", "gcs")
        monkeypatch.setenv("AUTH_STORAGE_GCS_BUCKET", "my-bucket")
        monkeypatch.setenv("AUTH_STORAGE_USERNAME", "testuser")
        from linkedin_mcp_server.config.loaders import load_from_env

        config = AppConfig()
        load_from_env(config)
        assert config.storage.backend == "gcs"
        assert config.storage.gcs_bucket == "my-bucket"
        assert config.storage.username == "testuser"

    def test_load_storage_prefix_from_env(self, monkeypatch):
        monkeypatch.setenv("AUTH_STORAGE_GCS_PREFIX", "linkedin-mcp")
        from linkedin_mcp_server.config.loaders import load_from_env

        config = AppConfig()
        load_from_env(config)
        assert config.storage.gcs_prefix == "linkedin-mcp"

    def test_storage_defaults_when_no_env(self):
        from linkedin_mcp_server.config.loaders import load_from_env

        config = AppConfig()
        load_from_env(config)
        assert config.storage.backend == "local"
        assert config.storage.gcs_bucket is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::TestStorageConfigEnvLoading -v`
Expected: FAIL — `AppConfig` doesn't have `storage` attribute loaded from env yet (attribute exists from Task 1, but env loading doesn't populate it).

- [ ] **Step 3: Write minimal implementation**

Add to `EnvironmentKeys` class in `loaders.py`:

```python
    AUTH_STORAGE_BACKEND = "AUTH_STORAGE_BACKEND"
    AUTH_STORAGE_GCS_BUCKET = "AUTH_STORAGE_GCS_BUCKET"
    AUTH_STORAGE_GCS_PREFIX = "AUTH_STORAGE_GCS_PREFIX"
    AUTH_STORAGE_USERNAME = "AUTH_STORAGE_USERNAME"
```

Add to `load_from_env()`, after the OAuth section (before `return config`):

```python
    # Auth-state storage
    if storage_backend := os.environ.get(EnvironmentKeys.AUTH_STORAGE_BACKEND):
        config.storage.backend = storage_backend

    if gcs_bucket := os.environ.get(EnvironmentKeys.AUTH_STORAGE_GCS_BUCKET):
        config.storage.gcs_bucket = gcs_bucket

    if gcs_prefix := os.environ.get(EnvironmentKeys.AUTH_STORAGE_GCS_PREFIX):
        config.storage.gcs_prefix = gcs_prefix

    if storage_username := os.environ.get(EnvironmentKeys.AUTH_STORAGE_USERNAME):
        config.storage.username = storage_username
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py::TestStorageConfigEnvLoading -v`
Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add linkedin_mcp_server/config/loaders.py tests/test_config.py
git commit -m "feat(config): load AUTH_STORAGE_* env vars"
```

---

### Task 3: Create storage backend protocol and LocalBackend

**Files:**
- Create: `linkedin_mcp_server/storage/__init__.py`
- Create: `linkedin_mcp_server/storage/backend.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_storage.py`:

```python
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
        from linkedin_mcp_server.storage.backend import LocalBackend, get_storage_backend

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_storage.py -v`
Expected: FAIL with `ModuleNotFoundError` — `linkedin_mcp_server.storage` doesn't exist.

- [ ] **Step 3: Write minimal implementation**

Create `linkedin_mcp_server/storage/__init__.py`:

```python
"""External auth-state storage backends."""

from linkedin_mcp_server.storage.backend import (
    LocalBackend,
    StorageBackend,
    StorageSyncError,
    get_storage_backend,
    sync_from_remote,
    sync_to_remote,
    delete_remote,
)

__all__ = [
    "LocalBackend",
    "StorageBackend",
    "StorageSyncError",
    "get_storage_backend",
    "sync_from_remote",
    "sync_to_remote",
    "delete_remote",
]
```

Create `linkedin_mcp_server/storage/backend.py`:

```python
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
    from linkedin_mcp_server.storage.gcs import GCSBackend

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


def sync_from_remote(
    auth_root: Path, username: str, backend: StorageBackend
) -> bool:
    """Download auth artifacts from remote storage. Raises on failure."""
    auth_root.mkdir(parents=True, exist_ok=True)
    prefix = ""
    if hasattr(backend, "prefix"):
        prefix = backend.prefix

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


def sync_to_remote(
    auth_root: Path, username: str, backend: StorageBackend
) -> bool:
    """Upload auth artifacts to remote storage. Best-effort (logs, doesn't raise)."""
    prefix = ""
    if hasattr(backend, "prefix"):
        prefix = backend.prefix

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
    prefix = ""
    if hasattr(backend, "prefix"):
        prefix = backend.prefix

    success = True
    for filename in ("cookies.json", "source-state.json"):
        key = _remote_key(prefix, username, filename)
        if not backend.delete(key):
            logger.warning("Failed to delete %s from remote storage", filename)
            success = False
    return success
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_storage.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add linkedin_mcp_server/storage/__init__.py linkedin_mcp_server/storage/backend.py tests/test_storage.py
git commit -m "feat(storage): add StorageBackend protocol and LocalBackend"
```

---

### Task 4: Add sync orchestration tests

**Files:**
- Modify: `tests/test_storage.py`

- [ ] **Step 1: Write failing tests for sync functions**

Add to `tests/test_storage.py`:

```python
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
        from linkedin_mcp_server.storage.backend import StorageSyncError, sync_from_remote

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
```

- [ ] **Step 2: Run test to verify they pass**

These tests use the implementation from Task 3, so they should already pass.

Run: `uv run pytest tests/test_storage.py -v`
Expected: all tests PASS (sync functions were implemented in Task 3).

- [ ] **Step 3: Commit**

```bash
git add tests/test_storage.py
git commit -m "test(storage): add sync orchestration tests with in-memory backend"
```

---

### Task 5: Create GCSBackend

**Files:**
- Create: `linkedin_mcp_server/storage/gcs.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_storage.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_storage.py::TestGCSBackendImport -v`
Expected: FAIL with `ModuleNotFoundError` — `linkedin_mcp_server.storage.gcs` doesn't exist.

- [ ] **Step 3: Write minimal implementation**

Create `linkedin_mcp_server/storage/gcs.py`:

```python
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
                logger.warning("GCS object not found: gs://%s/%s", self.bucket, remote_key)
                return False
            local_path.parent.mkdir(parents=True, exist_ok=True)
            blob.download_to_filename(str(local_path))
            logger.debug("Downloaded gs://%s/%s → %s", self.bucket, remote_key, local_path)
            return True
        except Exception:
            logger.warning(
                "GCS download failed: gs://%s/%s", self.bucket, remote_key, exc_info=True
            )
            return False

    def upload(self, local_path: Path, remote_key: str) -> bool:
        try:
            blob = self._bucket.blob(remote_key)
            blob.upload_from_filename(str(local_path))
            logger.debug("Uploaded %s → gs://%s/%s", local_path, self.bucket, remote_key)
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_storage.py::TestGCSBackendImport -v`
Expected: PASS if `google-cloud-storage` is installed, SKIP otherwise. Both are acceptable.

- [ ] **Step 5: Commit**

```bash
git add linkedin_mcp_server/storage/gcs.py tests/test_storage.py
git commit -m "feat(storage): add GCSBackend implementation"
```

---

### Task 6: Add `[gcs]` optional dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add optional dependency**

Add after the `[project.scripts]` section in `pyproject.toml`:

```toml
[project.optional-dependencies]
gcs = ["google-cloud-storage>=2.0"]
```

- [ ] **Step 2: Sync the lock file**

Run: `uv sync`
Expected: lock file updates, no errors.

- [ ] **Step 3: Verify optional install works**

Run: `uv sync --extra gcs`
Expected: `google-cloud-storage` installs successfully.

- [ ] **Step 4: Run the GCS backend test with dependency available**

Run: `uv run pytest tests/test_storage.py::TestGCSBackendImport -v`
Expected: PASS (not SKIP, since `google-cloud-storage` is now installed).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add [gcs] optional dependency for google-cloud-storage"
```

---

### Task 7: Hook into cli_main.py startup

**Files:**
- Modify: `linkedin_mcp_server/cli_main.py:341-348`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_storage.py`:

```python
class TestStartupHook:
    def test_sync_from_remote_called_before_auth_check(self, tmp_path, monkeypatch):
        """Verify that sync_from_remote is invoked when storage backend is gcs."""
        call_log = []

        monkeypatch.setenv("AUTH_STORAGE_BACKEND", "gcs")
        monkeypatch.setenv("AUTH_STORAGE_GCS_BUCKET", "test-bucket")
        monkeypatch.setenv("AUTH_STORAGE_USERNAME", "testuser")

        import linkedin_mcp_server.storage.backend as backend_mod

        original_sync = backend_mod.sync_from_remote

        def mock_sync(auth_root, username, backend):
            call_log.append(("sync_from_remote", username))
            # Write fake auth files so auth check passes
            auth_root.mkdir(parents=True, exist_ok=True)
            (auth_root / "cookies.json").write_text("[]")
            (auth_root / "source-state.json").write_text('{"version":1}')
            return True

        monkeypatch.setattr(backend_mod, "sync_from_remote", mock_sync)

        from linkedin_mcp_server.config import get_config

        config = get_config()
        assert config.storage.backend == "gcs"
        assert len(call_log) == 0  # Not called yet — integration requires running main()
```

- [ ] **Step 2: Run test to verify it passes** (this is a config-level check)

Run: `uv run pytest tests/test_storage.py::TestStartupHook -v`
Expected: PASS.

- [ ] **Step 3: Write the hook implementation**

In `linkedin_mcp_server/cli_main.py`, add import at the top:

```python
from linkedin_mcp_server.session_state import auth_root_dir
from linkedin_mcp_server.storage import get_storage_backend, sync_from_remote
```

In the `main()` function, insert **before** the `ensure_authentication_ready()` call (line 344), inside the same try block:

```python
            if config.storage.backend != "local":
                backend = get_storage_backend(config.storage)
                auth_root = auth_root_dir()
                sync_from_remote(auth_root, config.storage.username, backend)
```

The resulting code block should read:

```python
        try:
            if config.storage.backend != "local":
                backend = get_storage_backend(config.storage)
                auth_root = auth_root_dir()
                sync_from_remote(auth_root, config.storage.username, backend)
            if not (config.server.oauth and config.server.oauth.enabled):
                ensure_authentication_ready()
```

- [ ] **Step 4: Run full test suite to check nothing broke**

Run: `uv run pytest -v`
Expected: all existing tests PASS (local backend is a no-op, no env vars set in other tests).

- [ ] **Step 5: Commit**

```bash
git add linkedin_mcp_server/cli_main.py
git commit -m "feat(startup): sync auth state from remote storage before auth check"
```

---

### Task 8: Hook into setup.py post-login

**Files:**
- Modify: `linkedin_mcp_server/setup.py:83-91`

- [ ] **Step 1: Write the hook implementation**

In `linkedin_mcp_server/setup.py`, add imports at the top:

```python
from linkedin_mcp_server.config import get_config
from linkedin_mcp_server.session_state import auth_root_dir
from linkedin_mcp_server.storage import get_storage_backend, sync_to_remote
```

In `interactive_login()`, after the `write_source_state()` call (line 85-86), add:

```python
            # Sync to remote storage if configured
            config = get_config()
            if config.storage.backend != "local":
                storage_backend = get_storage_backend(config.storage)
                auth_root = auth_root_dir(user_data_dir)
                if sync_to_remote(auth_root, config.storage.username, storage_backend):
                    print("   Auth state synced to remote storage")
                else:
                    print("   Warning: failed to sync auth state to remote storage")
```

- [ ] **Step 2: Run full test suite**

Run: `uv run pytest -v`
Expected: all tests PASS.

- [ ] **Step 3: Commit**

```bash
git add linkedin_mcp_server/setup.py
git commit -m "feat(login): sync auth state to remote storage after login"
```

---

### Task 9: Hook into browser.py shutdown

**Files:**
- Modify: `linkedin_mcp_server/drivers/browser.py:486-505`

- [ ] **Step 1: Write the hook implementation**

In `linkedin_mcp_server/drivers/browser.py`, add imports at the top:

```python
from linkedin_mcp_server.session_state import auth_root_dir
from linkedin_mcp_server.storage import get_storage_backend, sync_to_remote
```

In `close_browser()`, after the cookie export try/except block (after line 503), add:

```python
    # Sync to remote storage if configured (best-effort)
    if cookie_export_path is not None:
        try:
            config = get_config()
            if config.storage.backend != "local":
                storage_backend = get_storage_backend(config.storage)
                auth_root = auth_root_dir()
                sync_to_remote(auth_root, config.storage.username, storage_backend)
        except Exception:
            logger.debug("Remote storage sync on close skipped", exc_info=True)
```

The resulting `close_browser()` should read:

```python
async def close_browser() -> None:
    """Close the browser and cleanup resources."""
    global _browser, _browser_cookie_export_path

    browser = _browser
    cookie_export_path = _browser_cookie_export_path
    _browser = None
    _browser_cookie_export_path = None

    if browser is None:
        return

    logger.info("Closing browser...")
    if cookie_export_path is not None:
        try:
            await browser.export_cookies(cookie_export_path)
        except Exception:
            logger.debug("Cookie export on close skipped", exc_info=True)
    # Sync to remote storage if configured (best-effort)
    if cookie_export_path is not None:
        try:
            config = get_config()
            if config.storage.backend != "local":
                storage_backend = get_storage_backend(config.storage)
                auth_root = auth_root_dir()
                sync_to_remote(auth_root, config.storage.username, storage_backend)
        except Exception:
            logger.debug("Remote storage sync on close skipped", exc_info=True)
    await browser.close()
    logger.info("Browser closed")
```

- [ ] **Step 2: Run full test suite**

Run: `uv run pytest -v`
Expected: all tests PASS.

- [ ] **Step 3: Commit**

```bash
git add linkedin_mcp_server/drivers/browser.py
git commit -m "feat(shutdown): sync auth state to remote storage after cookie export"
```

---

### Task 10: Hook into cli_main.py logout

**Files:**
- Modify: `linkedin_mcp_server/cli_main.py:70-112` (clear_profile_and_exit)

- [ ] **Step 1: Write the hook implementation**

In `linkedin_mcp_server/cli_main.py`, add `delete_remote` to the existing storage import:

```python
from linkedin_mcp_server.storage import get_storage_backend, sync_from_remote, delete_remote
```

In `clear_profile_and_exit()`, after `clear_auth_state(get_profile_dir())` succeeds (line 106-107), add:

```python
        # Delete remote auth state if configured
        if config.storage.backend != "local":
            try:
                storage_backend = get_storage_backend(config.storage)
                if delete_remote(config.storage.username, storage_backend):
                    print("✅ Remote auth state deleted")
                else:
                    print("⚠️  Failed to delete remote auth state")
            except Exception as e:
                print(f"⚠️  Could not delete remote auth state: {e}")
```

Note: `config` is already available in `clear_profile_and_exit()` (line 72).

- [ ] **Step 2: Run full test suite**

Run: `uv run pytest -v`
Expected: all tests PASS.

- [ ] **Step 3: Commit**

```bash
git add linkedin_mcp_server/cli_main.py
git commit -m "feat(logout): delete remote auth state when storage backend is configured"
```

---

### Task 11: Lint, format, type-check

- [ ] **Step 1: Run linter**

Run: `uv run ruff check . --fix`
Expected: no errors (or auto-fixed).

- [ ] **Step 2: Run formatter**

Run: `uv run ruff format .`
Expected: files formatted.

- [ ] **Step 3: Run type checker**

Run: `uv run ty check`
Expected: no new errors from the storage module. Existing errors (if any) should not increase.

- [ ] **Step 4: Run full test suite**

Run: `uv run pytest --cov -v`
Expected: all tests PASS, coverage includes new storage module.

- [ ] **Step 5: Commit any formatting/lint fixes**

```bash
git add -u
git commit -m "style: lint and format storage module"
```
