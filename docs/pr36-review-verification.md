# PR #36: HTTP factory review fixes and verification

## Baseline and dependency

The review was reproduced on PR #36 head `e458282b24ef81f05dd6577f336f0d018541c118`.
Complete collection failed on the four modules listed by the reviewer. Running
the importability and supported-version tests directly reproduced both additional
failures.

Refs recorded before implementation:

| Ref | Commit |
| --- | --- |
| PR #36 / `feature/issue-23-http-factory` | `e458282b24ef81f05dd6577f336f0d018541c118` |
| PR #34 head | `55512e0b8b5231b066b9adb6b2284bdfc0989160` |
| Upstream `develop` | `b3a2e13ae9f6af024f568b10d7c689927ae8ec2a` |

Upstream develop was integrated with normal merge commit `8df5fc4`. The merge
kept the sidecar files deleted. The supported-version test fix was already on
develop and arrived with that merge; no supported protocol date was removed.

PR #36 remains dependent on PR #34 and should land after it. PR #34 was not
modified, and its newer test exclusions were not imported. The separate
spillover branch and PR are not part of this change.

## Architecture and behavior

Official MCP 2.x remains the sole owner of `/mcp`: it parses JSON-RPC, dispatches
requests, and serializes responses. No removed ping helper, dispatch pipeline,
body-replay middleware, or wrapper was restored.

A small public `Server.middleware` hook applies era policy to the SDK's already
parsed request context. It rejects initialization on modern-only servers,
rejects explicit unsupported version claims, and checks that a supplied HTTP
version agrees with its envelope. It delegates valid calls to the official
runner. It does not read or buffer the HTTP request body, dispatch a second
handler, or construct JSON-RPC responses.

The official SDK uses an implicit `2025-03-26` version when compatible HTTP
requests omit a protocol header. That SDK fallback remains allowed for legacy
compatibility; it is not added as a newly advertised protocol date. Explicit
versions still follow the existing era policy.

| Era | Explicit supported dates | Resource subscribe capability |
| --- | --- | --- |
| legacy | 2025-06-18, 2025-11-25 | true; existing compatibility behavior |
| auto | 2025-06-18, 2025-11-25, 2026-07-28 | false; subscription requests rejected |
| modern | 2026-07-28 | false; subscription requests rejected |

The shared subscription predicate is used by discover, capabilities, and
subscribe/unsubscribe handling. Server instances receive the resolved era when
their handlers are configured, so capabilities are correct before HTTP startup,
for additional server instances, and during stdio initialization.

The existing single CORS layer, era-driven `/json/version`, official session
manager, health routes, slash compatibility, and public stdio runner remain.

## Test migration map

| Previous failure or test target | Replacement and retained coverage |
| --- | --- |
| Blueprint import of deleted dispatch classes | Real `server/discover` and tool calls through the mounted HTTP application; capabilities and no-session assertions retained |
| Deprecated-method tests using pipeline and replay doubles | Official HTTP dispatch and registered handlers; sessionless subscription rejection and legacy compatibility tested separately |
| Wire tests using deleted pipeline classes | Actual HTTP method/name/version rejection and successful supported-version requests; parsing, error mapping, identity, and pure validators retained |
| Stateless HTTP imports of deleted ping/dispatch helpers | Mounted-app tool/resource round trips, JSON and SSE responses, all eras, header-only malformed request rejection, unknown-method errors, CORS, slash/health/version checks, verified identity, parameter mirroring, and sessionless isolation |
| Test-module importability | Existing registry remains enabled and unchanged; all registered modules import |
| Supported versions per era | Existing additional legacy date included in test expectations through the develop merge |
| Private stdio dependency | AST checks of runner imports, real memory-stream exchanges, legacy initialization, modern calls, initialization rejection, notifications, and existing stdout safety tests |

The large test deletion reflects removal of tests of retired implementation
details and duplicate pipeline/replay entry points. It does not exclude files
from pytest. Pure header, CORS, parser, SSE, method-contract-table, and response
echo utility tests remain. Coverage of current behavior is exercised at the
official transport boundary instead of through a replacement sidecar.

MCP 2.2 maps modern method-not-found errors to HTTP 404; the legacy transport
maps those errors to HTTP 200 with a JSON-RPC error body. Tests assert those
official transport mappings rather than preserving a deleted sidecar's status.

## Verification

All tests used MCP **2.2.0**. Full runs used:

```text
python -m pytest tests --cov=nitrostack --cov-report=term-missing --timeout=60
```

| Platform | Python | Complete suite | Coverage |
| --- | --- | --- | --- |
| Windows | 3.12.14 | 807 passed | 86.02% |
| Ubuntu / WSL | 3.10.21 | 807 passed | 86.04% |
| Ubuntu / WSL | 3.11.16 | 807 passed | 86.02% |
| Ubuntu / WSL | 3.12.14 | 807 passed | 86.02% |

No tests were skipped, marked xfail, ignored, or deselected in these complete
runs. Coverage's separate "1 empty file skipped" message refers to an empty
source file in its report, not a skipped pytest test.

The final review restored two additional pre-existing pure utility checks:
the method-contract surface and supported response-version echo. Both passed
on all four runtimes after the complete runs above. Final collection contains
**809 tests**, with no collection errors. These two additions do not change
production behavior. The hosted workflow should run all 809 tests on the final
commit; local Linux matrix results above are not a claim about hosted CI status.

Other recorded checks:

- The six reviewer-reported test modules: **225 passed** before the two final
  utility checks were restored.
- The original issue verification command: **34 passed**.
- The issue #22/#23 ownership/factory and related HTTP/stdio/deprecation set:
  **76 passed**.
- AST validation rejects private `mcp.server.runner` imports.
- Production searches find no deleted sidecar wrappers or ping helpers.
- `git diff --check` passes.

The exact original issue verification command is:

```text
python -m pytest tests/test_transport_http.py tests/test_lifecycle_http.py tests/test_mcp20_stdio.py tests/test_transports.py -q
```

For a fresh environment, install the repository's development extra first:

```text
python -m pip install -e ".[dev]"
```

The existing GitHub workflow provides the Linux Python 3.10/3.11/3.12 matrix.
Its status must be checked separately on the pushed commit. No reviewer reply
was posted as part of the implementation; a response is prepared for user review.

## Rebase after PR #34 merged (2026-10-28)

PR #34 merged normally as `805db05`. PR #36 was rebased onto current develop
`63ecd9240d98bbe06c879089dd887494c7e99293`, which also includes the upstream
SecretValue work. The previous head `71e47f1` is preserved locally as
`backup/pr36-before-develop-rebase-20261028`.

The rebased branch preserves the migrated tests and single Starlette CORS layer.
Conflicts with the merged foundation's older tests/custom CORS layer were
resolved while retaining browser-defined Mcp-Param headers and origin checks.
Comparison against the previously tested merged foundation plus new develop
found no functional differences beyond these retained corrections.

Complete collection succeeds with **866 tests**. Exact-source local full suites
at rebased implementation head `682630b`, using MCP **2.2.0**, report:

| Platform | Python | Result | Coverage |
|---|---|---|---|
| Ubuntu / WSL | 3.10.21 | 866 passed | 86.11% |
| Ubuntu / WSL | 3.11.16 | 866 passed | 86.11% |
| Ubuntu / WSL | 3.12.14 | 866 passed | 86.11% |
| Windows | 3.12.14 | 866 passed | 86.11% |

No ignored files, deselections, skips or xfails were used. `git diff --check`
passes. Logs are retained locally in `.pytest_cache/rebase-linux-3.10.log`,
`rebase-linux-3.11.log`, `rebase-linux-3.12.log` and
`rebase-windows-full.tmp.log`. The hosted CI results above apply to the old head;
hosted CI must be checked again on the rebased published head. PR #34 is no
longer an unmerged dependency. PR #37 remains stacked on this PR until #36 lands.
