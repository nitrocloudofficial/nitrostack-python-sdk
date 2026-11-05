# Python file length limit investigation

**Measured commit:** `902c759e911771c4e01d85c5d0ba55650e79acca` (`develop`, 2026-11-04)

## Current measurement

Counted physical lines in every `*.py` file recursively under `nitrostack/`,
`tests/`, and `examples/`. The count includes blank lines, comments, docstrings,
and lines inside multiline strings; a final newline does not add a phantom line.
This matches `(Get-Content file).Count` for the files measured. Files are
reported only when they have **more than 500** physical lines.

| Area | Files over 500 |
| --- | ---: |
| `nitrostack/` (including its `templates/` and `cli/templates/` trees) | 7 |
| `tests/` | 6 |
| `examples/` | 1 |
| Template subtrees (subset of `nitrostack/`, not an additional area) | 0 |
| **Total** | **14** |

| File | Lines |
| --- | ---: |
| `nitrostack/core/app.py` | 2,273 |
| `nitrostack/cli/main.py` | 2,000 |
| `tests/test_cli.py` | 1,568 |
| `tests/test_oauth.py` | 1,011 |
| `nitrostack/widgets/route_templates.py` | 870 |
| `nitrostack/transports/http.py` | 797 |
| `tests/test_transports.py` | 732 |
| `tests/test_widgets.py` | 629 |
| `examples/flight_booking_server.py` | 582 |
| `tests/test_tasks.py` | 574 |
| `nitrostack/widgets/views.py` | 569 |
| `nitrostack/auth/oauth.py` | 516 |
| `tests/test_secret_value.py` | 515 |
| `nitrostack/cli/pack.py` | 507 |

The repository has Ruff configured in `pyproject.toml`, but the current GitHub
Actions workflow runs the test suite and coverage only; it does not run Ruff.
At the measured commit there was no `docs/` tree or CONTRIBUTING guide; this
investigation adds the first `docs/` file, and a CONTRIBUTING guide still does
not exist. The open PR list and changed files were checked on 2026-11-04 using
GitHub's public API. Two open PRs touch candidates for the proposed splits:

| Open PR | Changed over-limit files | Diff size in those files | Scheduling impact |
| --- | --- | ---: | --- |
| [#37, wire alignment](https://github.com/nitrocloudofficial/nitrostack-python-sdk/pull/37) | `nitrostack/core/app.py`, `nitrostack/transports/http.py` | `app.py`: +12/-32; `http.py`: +22/-6 | Let this PR land before splitting either file, then remeasure. |
| [#16, skip init prompts for non-TTY](https://github.com/nitrocloudofficial/nitrostack-python-sdk/pull/16) | `nitrostack/cli/main.py` | +10/-3 | Let this PR land before splitting the CLI module, then remeasure. |

The other open PRs at the time of the check (#110, #104, #98, and #58) did not
change any of the 14 files over 500 lines. The overlaps found are small by line
count, but they are direct edits to planned split targets, so sequence those
splits after the PRs merge. This check is a snapshot; repeat it when filing and
scheduling implementation work.

## Decision

Adopt a **500 physical-line default with a ratchet for existing offenders**.
The count is intentionally simple and reproducible. Counting only nonblank
lines or stripping comments/docstrings would add parser and policy complexity,
and still would not solve the embedded-template cases. Physical length is a
coarse reviewability signal, not a quality score; the ratchet avoids requiring
an all-at-once rewrite of the 14 existing files.

Scope the check to tracked, hand-maintained Python files under `nitrostack/`,
`tests/`, `examples/`, and `scripts/`. The first three trees were measured
above; `scripts/` is included prospectively and has no known large module at
this commit. Do not apply this policy to Markdown, HTML/CSS/JavaScript assets,
lockfiles, vendored dependencies, or generated output. Include test modules
under the same 500-line rule as production code: tests are Python modules and
the very large test files also need clearer ownership. Do not create a higher
test threshold. The baseline is the exception for the existing over-limit
files, not a permanent test exemption.

Do not create a blanket exemption for embedded templates. Count the physical
Python file containing them, including `route_templates.py` and the template
constants currently in `cli/main.py`. During any eventual extraction, moving
static HTML/JavaScript to the existing asset/data formats makes those assets
out of Python scope; keep Python dispatch and generation code under the normal
rule. Do not exempt an entire Python module just because it contains strings.

For each baseline offender, CI should fail if its physical line count grows
above the checked-in baseline. New Python files, and files once they are at or
below 500, should fail above 500. When an offender shrinks, lower its baseline
in the same change. This prevents regression without forcing unrelated files
to split immediately. Make any exceptional baseline increase explicit in code
review with a reason, and prefer to avoid such increases.

Markdown docs should not share this rule. Documentation length depends on
examples, reference material, and navigation; a per-file documentation limit
would be a separate policy question and would not help evaluate Python module
boundaries.

## Enforcement options

| Option | What it checks | Configuration and CI cost | Assessment |
| --- | --- | --- | --- |
| Ruff `PLR0915` | Statements in an individual function or method, not lines in a module. | Ruff is already configured, but enabling this rule only needs a per-function threshold and does not enforce the requested file policy. A Ruff invocation would also need to be added to CI. | Not suitable for this requirement. It may be considered separately for function complexity. [Ruff rule reference](https://docs.astral.sh/ruff/rules/too-many-statements/) |
| Pylint `too-many-lines` (`max-module-lines`) | Module line count, through Pylint's `C0302` checker. | Set `max-module-lines=500`, add Pylint as a development dependency, and select/check the intended files. A hard threshold would flag the current offenders immediately; the ratchet and scoped exceptions still need a baseline mechanism or per-file suppressions. Adding Pylint to CI also means maintaining its configuration and accepting its broader tool footprint. | Direct built-in check, but a poor fit for this repo's selected-file ratchet. [Pylint checker](https://pylint.readthedocs.io/en/v2.17.7/user_guide/messages/convention/too-many-lines.html) |
| Flake8 `flake8-file-length` plugin | `FLN001` checks file line count; it can optionally ignore blank lines and comments. The defaults count them, matching the proposed physical-line definition. | Add Flake8 and `flake8-file-length`, select `FLN`, set `--max-file-length=500`, and scope the invocation. CI also needs the existing-offender ratchet, which this plugin's single maximum does not supply, so a baseline mechanism or suppressions are still required. | A viable off-the-shelf hard check ([plugin options](https://pypi.org/project/flake8-file-length/)); its published version is 0.1.0 at review time. It adds two packages/tooling layers and still needs custom ratchet logic. |
| Small custom script | Exactly the defined physical-line count, paths, 500 threshold, and existing-file baseline. | Add a stdlib-only script plus a baseline manifest; invoke it as one short step in the existing CI job. No lint dependency is needed. The script and baseline need maintenance when files shrink or the policy changes. | Recommended: semantics and ratchet are explicit, inexpensive, and match the requested scope. |

The check is a fast local filesystem scan and has negligible CI cost relative to
the existing three-version pytest matrix. The current workflow's Python 3.10,
3.11, and 3.12 jobs would each run it once. It should report the actual path,
count, allowed count, and whether the violation is a new file or baseline
growth. Keep it independent of test collection so the policy has one clear
failure mode.

## Rough split plan and risks

The line counts identify candidates, not mandatory split boundaries. Make
splits topic-based, retain focused tests, and preserve current import paths with
facade re-exports where callers may depend on them.

| File | Candidate modules / boundaries | Main risks |
| --- | --- | --- |
| `nitrostack/core/app.py` (2,273) | Extract schema/input normalization and request-context metadata helpers first. Then separate tool registration/definition and invocation/result conversion from resource/prompt handling and task protocol handlers. Leave `McpApplication` orchestration and `McpApplicationFactory` in the current module or a thin facade. | The large `McpApplication` class shares state across registration, calls, tasks, and startup. Moving methods without clarifying ownership can create import cycles or split tightly coupled state. Preserve imports such as `nitrostack.core.app.McpApplication`, `McpApplicationFactory`, and existing helper exports. Internal modules must import implementation modules directly, not the facade. |
| `nitrostack/cli/main.py` (2,000) | Move the module-level generated-project template constants (currently much of the first ~950 lines) into focused template resources/modules. Separate init/dev/start, generation/validation/pack/upgrade, and client registration command handlers. Keep argument parsing and `main()` as the small entry point. | Generated output must stay byte-for-byte compatible where tests or users rely on it. Preserve the console entry point `nitrostack.cli.main:main` and `python -m nitrostack.cli.main`; avoid internal imports back through `main`. CLI tests may import helper names directly. |
| `tests/test_cli.py` (1,568) | Split by CLI lifecycle/init and ports, generation and template output, packaging/upgrades/validation, and client registration. Move shared fixtures/helpers to `tests/` support only when more than one module needs them. | Keep setup local enough that test intent remains clear. Avoid cross-importing test modules, which makes collection/order brittle. |
| `tests/test_oauth.py` (1,011) | Group PKCE/guard validation, OAuth metadata/discovery and dynamic registration, and token introspection/JWKS/cache behavior. | Shared server/client fixtures and environment cleanup must remain isolated; retain full end-to-end coverage of network-facing behavior. |
| `nitrostack/widgets/route_templates.py` (870) | Separate generic widget HTML, pizza widgets, and flight/order widgets into data or template modules; keep route selection/rendering in a small dispatcher. | Embedded HTML/JavaScript escaping and exact rendered output can break. Route aliases and current widget builder imports need compatibility coverage. |
| `nitrostack/transports/http.py` (797) | Move middleware classes into a middleware module and split HTTP app assembly into focused helpers for CORS/headers, OAuth, widgets/landing routes, and MCP transport setup. Keep the existing module as a compatibility facade. | Middleware ordering, shared state, and protocol header behavior are order-sensitive. Preserve `nitrostack.transports.http` imports and cover sessionful/stateless setup. |
| `tests/test_transports.py` (732) | Split sessionful/stateless HTTP and lifecycle cases, protocol/header compatibility, and dual transport/progress tests. | Reuse server fixtures without coupling test modules; preserve clean shutdown and port isolation. |
| `tests/test_widgets.py` (629) | Split route rendering, widget resource registration, metadata/host integration, and template output assertions. | Ensure route coverage and exact output/security assertions remain together with their behavior. |
| `examples/flight_booking_server.py` (582) | Separate example domain data/services, MCP module/controller definitions, and startup/configuration, or move the reusable pieces into example-local modules. | Keep the example runnable as documented and avoid turning sample modules into accidental SDK public APIs. |
| `tests/test_tasks.py` (574) | Split task lifecycle/progress, authorization and cancellation, and store/eviction behavior if those topics are independently understood. | Shared async fixtures and task semantics can span store and transport layers; do not split just to meet a number. |
| `nitrostack/widgets/views.py` (569) | Split route/view dispatch from response construction and static preview/resource handling. | Route registration and view names may be imported elsewhere; retain exports and avoid circular imports with widget registry code. |
| `nitrostack/auth/oauth.py` (516) | Split protocol/data types and token/client/cache operations only if their dependencies are one-directional. | OAuth types and helpers are interdependent; preserve imports from `nitrostack.auth.oauth` and avoid introducing import cycles across auth modules. |
| `tests/test_secret_value.py` (515) | Split construction/serialization, redaction and error behavior, and integration/security boundaries. | Security regression cases should stay discoverable; avoid splitting paired input/output assertions. |
| `nitrostack/cli/pack.py` (507) | Separate file selection/ignore rules from vendoring and wheel assembly, if each boundary has a stable interface. | Packaging behavior depends on filesystem cleanup and manifest details. Preserve CLI imports and generated wheel contents. |

These are rough candidate topics from current names and structure. Before a
split, inspect the exact callers and refresh the open-PR file list. No split is
part of this investigation change.

## Follow-up implementation issues

**Conclusion: B. Open implementation issues.** Fourteen files exceed 500 lines,
including two production modules above 2,000 lines and multiple very large test
modules. A ratchet makes a useful review boundary enforceable without forcing
all existing files to be split at once.

Implementation issue scopes:

1. [**Enforce the Python module-length ratchet in CI**](https://github.com/nitrocloudofficial/nitrostack-python-sdk/issues/111) (**open**). Add a stdlib script and
   checked-in baseline for current offenders; enforce 500 physical lines for
   new Python files; block baseline growth; reduce baselines as files shrink;
   add the CI step; document the scope and update/create `CONTRIBUTING.md`.
   Out of scope: splitting files or changing line counting to logical SLOC.
2. [**Split core application and CLI modules behind compatibility facades**](https://github.com/nitrocloudofficial/nitrostack-python-sdk/issues/112) (**open, umbrella**). Its two implementation scopes are tracked as standalone issues:
   [CLI `main.py` and flight-booking example (#119)](https://github.com/nitrocloudofficial/nitrostack-python-sdk/issues/119)
   and [core `app.py` (#120)](https://github.com/nitrocloudofficial/nitrostack-python-sdk/issues/120).
   Preserve the CLI entry point and `nitrostack.core.app` compatibility imports.
3. [**Split remaining over-limit Python files by responsibility**](https://github.com/nitrocloudofficial/nitrostack-python-sdk/issues/113) (**open, umbrella**). The implementation scopes are tracked as standalone issues:
   [widget route templates and views (#121)](https://github.com/nitrocloudofficial/nitrostack-python-sdk/issues/121),
   [OAuth (#122)](https://github.com/nitrocloudofficial/nitrostack-python-sdk/issues/122),
   [CLI packaging (#123)](https://github.com/nitrocloudofficial/nitrostack-python-sdk/issues/123),
   and [HTTP transport (#125)](https://github.com/nitrocloudofficial/nitrostack-python-sdk/issues/125).
   The standalone issues exclude tests. The flight-booking example is included
   in #119; other example-related files remain out of scope.

Before starting an overlapping split, refresh open-PR changed-file lists and
remeasure from the latest `develop`. PRs #37 and #16 were previously identified
as overlapping the core app, HTTP transport, and CLI main targets; confirm their
current status before work begins. The large test modules listed above are not
included in issues #119–#125 and need separate scoping if they are to be split.
