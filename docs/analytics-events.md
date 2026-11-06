# NitroStack Python SDK CLI — analytics events

Telemetry is **opt-in by configuration**: set `NITROSTACK_POSTHOG_API_KEY` to enable. It is **off** when the key is unset, when `DO_NOT_TRACK=1`, when `NITROSTACK_TELEMETRY=0` (or `false`/`off`/`no`), during pytest (`PYTEST_CURRENT_TEST`), and in CI unless you explicitly enable it.

## Super properties

Sent on every captured event:

| Property | Description |
|----------|-------------|
| `product` | `python_sdk` |
| `surface` | `terminal` |
| `environment` | `production`, `staging`, or `development` (from `NITROSTACK_ENVIRONMENT` or heuristics) |
| `app_version` | Installed `nitrostack` package version |
| `python_version` | Interpreter version |
| `sdk_version` | Same as `app_version` |
| `os_platform` | `sys.platform` |
| `os_arch` | `platform.machine()` |

## Events

| Event | When | Properties |
|-------|------|------------|
| `cli_command_invoked` | Every subcommand after parse | `command`: subcommand name (`init`, `pack`, …) |
| `cli_init_completed` | `init` finishes successfully | `template`, `skip_install`, `duration_ms`, `has_custom_description` |
| `cli_login_completed` | After `nitrostack-py login` succeeds (identity helper) | `user_email`, `cloud_user_id` (+ super properties) |
| `cli_deploy_completed` | `pack` writes a valid wheel | none beyond super properties |
| `cli_deploy_failed` | `pack` raises before success | `error_code`: exception type name (truncated) |

## Identity

- **Anonymous:** distinct id = first 16 hex chars of SHA-256(`hostname`:`username`), same scheme as the TypeScript CLI.
- **Login:** `login` runs `alias(anonymous, user_id)`, `identify(user_id, { email })`, optional `group_identify('organization', org_id)`, then `cli_login_completed`. Subsequent events use the Cloud user id as distinct id.

## Code locations

- Module: `nitrostack/cli/telemetry.py`
- CLI wiring: `nitrostack/cli/main.py` (`track_command_invoked`, `init`, `login`, `shutdown_telemetry`)
- Deploy: `nitrostack/cli/pack.py` (`cli_deploy_*`)
