# Design: GCS-backed auth state persistence

Resolves: [#7](https://github.com/5queezer/linkedin-mcp-server/issues/7)

## Problem

Cloud Run cold starts wipe the filesystem. The server loses `cookies.json` and `source-state.json`, making LinkedIn auth unrecoverable without manual `--login`.

## Key finding

LinkedIn OAuth does not cover messaging/inbox access. The existing browser-based auth (cookie extraction) is the only viable approach. The repo's OAuth support protects the MCP endpoint only — it is separate from LinkedIn auth.

## Solution

Persist portable auth artifacts (`cookies.json` + `source-state.json`) to Google Cloud Storage. Restore on startup, re-sync after login and on shutdown.

Cloud Run always runs as a **foreign runtime** — it bridges from cookies on every cold start. No full browser profile is persisted (avoids 50-200MB transfers and cross-platform issues).

## Configuration

```
AUTH_STORAGE_BACKEND=local|gcs          # default: local (no-op)
AUTH_STORAGE_GCS_BUCKET=my-bucket       # required when backend=gcs
AUTH_STORAGE_GCS_PREFIX=linkedin-mcp    # optional, default: empty
AUTH_STORAGE_USERNAME=williamhgates     # required when backend != local
```

GCS object layout:

```
gs://{bucket}/{prefix}/{username}/cookies.json
gs://{bucket}/{prefix}/{username}/source-state.json
```

## Architecture

### StorageBackend protocol

```python
class StorageBackend(Protocol):
    def download(self, remote_key: str, local_path: Path) -> bool: ...
    def upload(self, local_path: Path, remote_key: str) -> bool: ...
    def delete(self, remote_key: str) -> bool: ...
```

Implementations:
- `LocalBackend`: no-op, all methods return `True`
- `GCSBackend`: uses `google-cloud-storage`, authenticates via ADC (automatic on Cloud Run)

### Package structure

```
linkedin_mcp_server/
├── storage/
│   ├── __init__.py      # exports public API
│   ├── backend.py       # StorageBackend protocol, LocalBackend, StorageSyncError
│   └── gcs.py           # GCSBackend (lazy import)
```

`google-cloud-storage` is an optional `[gcs]` extra dependency.

### Sync operations

| Function | When called | Behavior on failure |
|----------|-------------|---------------------|
| `sync_from_remote()` | Startup, before auth validation | **Fail hard** — raise StorageSyncError |
| `sync_to_remote()` | After `--login`, on `close_browser()` shutdown | **Best-effort** — log warning, don't crash |
| `delete_remote()` | During `--logout` | Log warning on failure |

### Hook points

**`cli_main.py` — startup:** Call `sync_from_remote()` before `ensure_authentication_ready()`.

**`setup.py` — post-login:** Call `sync_to_remote()` after `write_source_state()` and `export_cookies()`.

**`browser.py` — shutdown:** Call `sync_to_remote()` after `export_cookies()` in `close_browser()`.

## Constraints

- 10s Cloud Run shutdown grace period is sufficient for cookie export + KB-sized GCS upload
- GCS default encryption (Google-managed AES-256) — no KMS
- `AUTH_STORAGE_USERNAME` env var required because at cold start there is no local state to extract a username from
- Config validation fails fast if `backend=gcs` but `gcs_bucket` or `username` is missing

## Testing

- Unit tests with mock `StorageBackend` (in-memory dict)
- Test sync_from raises on download failure
- Test sync_to logs but doesn't raise on upload failure
- Test config validation rejects incomplete GCS config
- No live GCS tests in CI

## Decision log

| # | Decision | Alternatives | Rationale |
|---|----------|-------------|-----------|
| 1 | Portable artifacts only | Full auth root, +derived snapshot | KB transfers, no cross-platform issues, bridge path works |
| 2 | StorageBackend protocol | No abstraction, lifecycle manager | Low overhead, extensible, testable via mock |
| 3 | Optional `[gcs]` extra | Required dependency | Keep base package lightweight |
| 4 | Sync on shutdown + after login | +periodic timer | Covers both flows without background complexity |
| 5 | Fail startup if GCS download fails | Fall back to local | Remote state is source of truth when configured |
| 6 | Best-effort upload on shutdown | Fail hard | Don't crash server over transient GCS error |
| 7 | Username via env var | Extract from cookies | No local state at cold start to extract from |
| 8 | User-keyed by LinkedIn username | SHA256 hash | Human-readable, auditable |
| 9 | GCS default encryption | Customer-managed KMS | Sufficient, no extra config |
| 10 | 10s shutdown grace | 30s | KB upload well within window |
