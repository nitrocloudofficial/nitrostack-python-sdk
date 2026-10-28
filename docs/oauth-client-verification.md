# Issue #68 verification

Validation date: 2026-10-28. Feature branch:
`feature/issue-68-oauth-client`, based directly on upstream `develop` at
`b3a2e13ae9f6af024f568b10d7c689927ae8ec2a`. PR #36 and spillover commits are not
part of this branch.

## Scope and checks

The three new test modules add 78 cases to the unchanged 889-test baseline.
Complete collection succeeds with 967 tests. The new feature selection passes
all 78 tests on Windows Python 3.12.14. It covers token records and expiry,
plaintext/encrypted stores, atomic failure recovery, concurrent writers,
authorization and client credentials, refresh rotation and cancellation,
revocation, registration, discovery, bounded urllib responses, DNS pins, TLS
identity, ports, redirect rejection and secret-safe output.

`git diff --check`, compilation/AST checks, public-export inspection and
`uv lock --check` pass. Runtime requirements are unchanged; cryptography is an
optional encryption extra and is also included in development dependencies.
No HTTP client dependency was added. No test exclusions, skips or xfail markers
were added.

## Complete suites

All runs use MCP 2.2.0 and install `.[dev,token-encryption]`. Linux runs use Ubuntu
under WSL. These are local compatibility runs, not hosted GitHub Actions results.

| Platform | Python | Result | Total coverage |
|---|---|---|---|
| Linux | 3.10.21 | 967 passed | 86.38% |
| Linux | 3.11.16 | 967 passed | 86.36% |
| Linux | 3.12.14 | 967 passed | 86.36% |
| Windows | 3.10.21 | 967 passed | 86.37% |
| Windows | 3.11.16 | 967 passed | 86.35% |
| Windows | 3.12.14 | 966 passed, 1 failed | 86.35% |

Commands, with an environment-specific interpreter and temporary directory:

```shell
python -m pytest tests --collect-only -o addopts= -q
python -m pytest tests/test_token_store.py tests/test_oauth_client.py tests/test_oauth_client_transport.py -o addopts= -q --timeout=60
python -m pytest tests --cov=nitrostack --cov-report=term-missing --timeout=60 --tb=short
git diff --check
uv lock --check
```

Full-run logs are retained locally in `.pytest_cache/linux-3.10.log`,
`linux-3.11.log`, `linux-3.12.log`, `windows-3.10.log`, `windows-3.11.log`, and
`windows-final.log`. The cache is ignored by Git.

## Windows socket limitation

The final Windows 3.12 run fails the unchanged
`tests/test_oauth.py::test_discovery_server_idempotent_start_and_dcr_http` while
reading a live localhost POST response: `ConnectionAbortedError: [WinError 10053]`.
Two earlier complete feature runs each reported 964 passed and one failure in
`test_discovery_dcr_404_when_disabled_or_missing_client_id` with the same socket
error. Those runs preceded the final two additional storage cases.

Comparison used an isolated archive of the exact unchanged develop commit and
the same Windows 3.12.14 environment/MCP version:

- Two complete baseline runs passed all 889 tests (85.78% coverage).
- The failing disabled-registration test passed five isolated repetitions on
  both baseline and feature code.
- Running the existing OAuth/PKCE/CIMD/authentication modules together on the
  unchanged baseline reproduced WinError 10053 in the disabled-registration test:
  83 passed, 1 failed.
- The corresponding feature selection reported 160 passed, 2 failed, both live
  discovery socket tests. The new feature-only selection remained 78 passed.

Baseline reproduction command:

```shell
python -m pytest tests/test_oauth.py tests/test_pkce.py tests/test_mcp20_oauth_cimd.py tests/test_auth_modules.py tests/test_oauth_discovery.py -o addopts= -q --timeout=60 --tb=short
```

This demonstrates an intermittent baseline socket failure under matching local
conditions. It does not establish the operating-system-level cause, and the
complete Windows 3.12 suite is not reported as passing. No socket test, server
implementation or assertion was changed to conceal the result.

## Publication status

Implementation and local validation were completed before publication. Branch
publication was subsequently authorized by the user. No PR creation or reviewer
message is included. Hosted CI remains to be verified on the published head;
the local matrix results above are not hosted CI results.
