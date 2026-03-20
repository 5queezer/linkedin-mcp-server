# Issue #7: GCS-backed auth state persistence plan

## Summary

This repository currently uses a Patchright browser profile plus exported cookies as the source LinkedIn authentication state. That state is stored under `~/.linkedin-mcp/` and can be bridged into foreign runtimes such as Docker containers.

That solves local portability, but not Cloud Run cold starts. When the filesystem disappears, the server loses the source profile, cookie export, and derived runtime artifacts.

## Key finding

LinkedIn OAuth is **not** a drop-in replacement for the current messaging tools.

The repository already includes OAuth support, but that OAuth flow is for protecting the MCP endpoint exposed to remote clients such as Claude. It is not LinkedIn member OAuth for inbox access.

For issue #7, the practical path is to persist the existing auth artifacts externally.

## Recommended direction

Add a pluggable auth-state storage backend and implement Google Cloud Storage first.

Persist the full auth root, not only `cookies.json`:

- source profile directory
- `cookies.json`
- `source-state.json`
- `runtime-profiles/` snapshots when present

## Proposed config surface

Environment variables:

- `AUTH_STORAGE_BACKEND=local|gcs`
- `AUTH_STORAGE_GCS_BUCKET=<bucket-name>`
- `AUTH_STORAGE_GCS_PREFIX=<optional/prefix>`

Optional later:

- `AUTH_STORAGE_GCS_KMS_KEY=<kms-key-resource>`
- `AUTH_STORAGE_SYNC_ON_CLOSE=true|false`

## Proposed module

Add `linkedin_mcp_server/auth_storage.py` with:

- `sync_from_remote_if_configured() -> bool`
- `sync_to_remote_if_configured() -> bool`
- `delete_remote_if_configured() -> bool`

Implementation approach:

1. Resolve the auth root from the configured source profile directory.
2. Archive the auth root into a deterministic tar.gz snapshot.
3. Upload/download a single object from GCS.
4. Restore atomically into the local auth root.
5. Never partially overwrite local state.

## Hook points

### Startup

Before calling `get_authentication_source()` or creating a browser, restore the latest remote snapshot when GCS storage is enabled.

### After login

After `write_source_state()` and cookie export succeed, upload a fresh snapshot.

### Shutdown

After browser cookie export during `close_browser()`, sync the auth root back to GCS.

### Logout

When clearing auth state, also remove the remote snapshot.

## Why full-root persistence is preferable

Persisting only `cookies.json` is weaker than persisting the whole auth root because the current runtime model already uses:

- source profile contents
- portable cookie export
- source session metadata
- derived runtime checkpoint state

Keeping the full auth root preserves parity with local behavior and avoids inventing a second auth lifecycle for remote deployments.

## Minimal implementation order

1. Add config schema and env loader support for `AUTH_STORAGE_*`.
2. Add storage abstraction with a local no-op backend and a GCS backend.
3. Restore from GCS before auth validation at startup.
4. Upload to GCS after `--login` completes.
5. Upload to GCS on browser shutdown.
6. Delete remote state during logout.
7. Add tests for config parsing and storage sync hooks.

## Notes on scope

This change does **not** replace the current scraping-based LinkedIn session model.

It is intended to make the existing model survive Cloud Run cold starts and other ephemeral filesystem environments.