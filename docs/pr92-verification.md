# PR #92 review corrections and verification

Validated locally on 2026-10-29 with official MCP **2.3.0**.

## History and scope

The original PR head was `3ea98a767e01016134fe4d0396518a91b6e7bff5`.
Only its spillover feature commit was replayed onto upstream `develop` at
`63ecd9240d98bbe06c879089dd887494c7e99293`. Unrelated inherited `main` commits
were excluded. The feature branch contains the rebased implementation, transport
ownership corrections/tests, and updated documentation. No version, dependency,
CLI, widget-registry or unrelated feature-branch changes are included.

## Independently reproduced failure and correction

The original 39 spillover cases exercised stores and registered handlers
in-process. They did not prove anonymous retrieval over a real HTTP session.
An independent legacy HTTP probe initialized the official application, called a
large-result tool, and read the returned URI using the same server-issued
`Mcp-Session-Id`. The tool returned a spillover envelope but the resource read
failed: per-request SDK session proxy objects received different ownership keys.

Production HTTP ownership now uses the shared session-ID resolver only on the
sessionful wire after the official manager has validated/routed the request.
Modern/auto HTTP requires verified identity for spilling. Invalid credentials
cannot fall back to an anonymous owner. Principal ownership retains issuer,
subject/client and tenant dimensions and works after reconnecting.

Stdio assigns an internal random connection ID for the lifetime of a stream pair.
The context adapter preserves each message's sender context while binding that
ID, so MCP 2.3's context propagation cannot discard it. Messages pass unchanged
to official MCP dispatch; the adapter does not parse JSON-RPC or own methods.
The ID is reset at shutdown, separate connections are isolated, and concurrent
HTTP requests cannot borrow stdio ownership.

## Results

Complete collection succeeds: **907 tests**. No ignores, deselections, new skips
or `xfail` markers were introduced in complete-suite runs.

| Environment | Full-suite result | Coverage |
|---|---|---|
| Linux Python 3.10.21 | 907 passed | 86.68% |
| Linux Python 3.11.16 | 907 passed | 86.63% |
| Linux Python 3.12.14 | 907 passed | 86.66% |
| Windows Python 3.10.21, sequential rerun | 906 passed, 1 skipped | 86.68% |
| Windows Python 3.11.16 | 906 passed, 1 skipped | 86.66% |
| Windows Python 3.12.14, sequential rerun | 906 passed, 1 skipped | 86.63% |

Linux results are local WSL Ubuntu runs, not hosted GitHub Actions. The Windows
skip is the existing real-filesystem symlink test: this account lacks link
creation privileges. The simulated rejection test passes on Windows and the
actual filesystem test passes on Linux.

- Existing spillover cases: **38 passed, 1 skipped** on Windows.
- New transport module: **43 passed**, including **24 parameterized large-output
  cases** across memory/file stores. These are included in the complete suite;
  the 24 cases are not additional to the 43.
- Combined spillover, stdio, task, pipeline, transform and widget selection:
  **146 passed, 1 skipped** on Windows.
- `git diff --check` passes; public exports and upstream transport behavior are
  exercised by the complete suite.

### Earlier Windows failures and their investigation

Initial Windows versions were run concurrently in the same checkout. The 3.10
run reported **905 passed, 1 failed, 1 skipped**: existing production logger tests
share `test_warn.log`, and one process deleted another process's test file. The
3.10 complete suite passed when rerun sequentially. Existing logger tests were
not changed to hide this test-run orchestration collision.

The initial 3.12 run reported **905 passed, 1 failed, 1 skipped**: the existing
`test_discovery_dcr_404_when_disabled_or_missing_client_id` raised a localhost
`ConnectionAbortedError: [WinError 10053]`. Its complete sequential rerun passed.

The two existing OAuth discovery socket tests were also run together three times
against an unchanged archive of upstream `63ecd924...` using Python 3.12.14 and
MCP 2.3.0. Attempts 1 and 3 reproduced the **same DCR test and WinError 10053**
(1 passed, 1 failed); attempt 2 passed both tests. This confirms baseline
intermittence on this host without establishing its underlying cause. No OAuth
implementation or existing test was modified.

## Reproduction

```powershell
python -m pytest tests --collect-only -o addopts= -q
python -m pytest tests/test_spillover.py tests/test_spillover_transport.py -o addopts=--timeout=60 -q
python -m pytest tests --cov=nitrostack --cov-report=term-missing --timeout=60 --tb=short -o addopts= -q
git diff --check
```

Run the complete command for Python 3.10, 3.11 and 3.12. Run Windows matrices
sequentially or in separate source directories because existing tests share
filenames in the current working directory.

Large-output tests generate results inside the tool from a small case identifier,
then check preview limits, original UTF-8 byte sizes, full resource bytes and
other-session denial. HTTP request-body limits are left unchanged. Cases cover
64 KiB boundaries, 1/5/10 MiB text, Unicode/escaped strings, 100,000 rows, 50,000
dictionary entries, approximately 6 MiB nested data, and rejection at 10 MiB plus
one byte and 20 MiB. The adjacent JSON report names the committed tests.

Local logs are retained in `D:/NitroStack/pr92-*.log` and the ignored
`.pytest_cache/pr92-linux-3.*.log`. Hosted CI and the existing PR description must
be verified/updated after publication is authorized; no remote rewrite or
reviewer message was performed during this local implementation.
