"""PYTHONSDK-11 epic and acceptance-criteria registry (Doc 11 / Plane ticket)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Iterable

from nitrostack.protocol.version import MODERN_PROTOCOL_VERSION


class ImplementationEpic(IntEnum):
    """Seven implementation epics from TICKET_PLANE_PROJECT_MANAGEMENT.md."""

    CORE_JSONRPC = 1
    STATELESS_HTTP = 2
    REGISTRIES_SCHEMA = 3
    ASYNC_TASKS = 4
    MULTI_TENANT = 5
    OAUTH_CIMD = 6
    MRTR_OBSERVABILITY = 7


@dataclass(frozen=True)
class EpicDeliverable:
    epic: ImplementationEpic
    item: str
    spec_doc: str
    test_module: str


@dataclass(frozen=True)
class AcceptanceCriterion:
    key: str
    description: str
    test_module: str


EPIC_DELIVERABLES: tuple[EpicDeliverable, ...] = (
    EpicDeliverable(
        ImplementationEpic.CORE_JSONRPC,
        "JSON-RPC error code hierarchy (-32700..-32603, -32020)",
        "02_JSONRPC_AND_WIRE_SPECIFICATION.md",
        "tests/test_mcp20_doc02_jsonrpc_wire.py",
    ),
    EpicDeliverable(
        ImplementationEpic.CORE_JSONRPC,
        "SEP-2164 missing resource returns -32602",
        "02_JSONRPC_AND_WIRE_SPECIFICATION.md",
        "tests/test_mcp20_doc03_contracts.py",
    ),
    EpicDeliverable(
        ImplementationEpic.CORE_JSONRPC,
        "Reject tasks/result and tasks/list on modern wire",
        "02_JSONRPC_AND_WIRE_SPECIFICATION.md",
        "tests/test_mcp20_doc05_tasks.py",
    ),
    EpicDeliverable(
        ImplementationEpic.STATELESS_HTTP,
        "POST /mcp stateless ingress without Mcp-Session-Id",
        "01_STATELESS_HTTP_AND_LIFECYCLE.md",
        "tests/test_mcp20_doc01_stateless_http.py",
    ),
    EpicDeliverable(
        ImplementationEpic.STATELESS_HTTP,
        "server/discover and ping fast path",
        "01_STATELESS_HTTP_AND_LIFECYCLE.md",
        "tests/test_mcp20_doc01_stateless_http.py",
    ),
    EpicDeliverable(
        ImplementationEpic.STATELESS_HTTP,
        "CORS headers for MCP preflight",
        "01_STATELESS_HTTP_AND_LIFECYCLE.md",
        "tests/test_mcp20_doc01_stateless_http.py",
    ),
    EpicDeliverable(
        ImplementationEpic.REGISTRIES_SCHEMA,
        "JSON Schema 2020-12 with depth bounding (max 64)",
        "03_TOOL_RESOURCE_PROMPT_CONTRACTS.md",
        "tests/test_mcp20_doc03_contracts.py",
    ),
    EpicDeliverable(
        ImplementationEpic.REGISTRIES_SCHEMA,
        "Resource URI templates and resolution",
        "03_TOOL_RESOURCE_PROMPT_CONTRACTS.md",
        "tests/test_mcp20_doc03_contracts.py",
    ),
    EpicDeliverable(
        ImplementationEpic.ASYNC_TASKS,
        "Task state machine and tasks/get embedded result",
        "05_MCP_TASKS_PROTOCOL_AND_LIFECYCLE.md",
        "tests/test_mcp20_doc05_tasks.py",
    ),
    EpicDeliverable(
        ImplementationEpic.ASYNC_TASKS,
        "Pluggable TaskStore and terminal-only TTL eviction",
        "06_TASK_STORE_AND_DISTRIBUTED_PERSISTENCE.md",
        "tests/test_mcp20_doc06_task_store.py",
    ),
    EpicDeliverable(
        ImplementationEpic.MULTI_TENANT,
        "TaskAccessContext isolation and anti-enumeration",
        "07_TASK_AUTHORIZATION_AND_TENANT_ISOLATION.md",
        "tests/test_mcp20_doc07_task_authorization.py",
    ),
    EpicDeliverable(
        ImplementationEpic.OAUTH_CIMD,
        "CIMD resolver with SSRF defenses and RFC 9207 iss",
        "08_OAUTH21_CIMD_AND_SECURITY.md",
        "tests/test_mcp20_doc08_oauth_cimd.py",
    ),
    EpicDeliverable(
        ImplementationEpic.MRTR_OBSERVABILITY,
        "MRTR input_required elicitation helpers",
        "04_MRTR_MULTI_ROUND_TRIP_SPEC.md",
        "tests/test_mcp20_doc04_mrtr.py",
    ),
    EpicDeliverable(
        ImplementationEpic.MRTR_OBSERVABILITY,
        "Extensions map, trace context, and cache hints",
        "09_EXTENSIONS_CACHE_AND_OBSERVABILITY.md",
        "tests/test_mcp20_doc09_extensions_cache_observability.py",
    ),
)

ACCEPTANCE_CRITERIA: tuple[AcceptanceCriterion, ...] = (
    AcceptanceCriterion(
        "stateless",
        "No Mcp-Session-Id emitted; stateless POST /mcp works",
        "tests/test_mcp20_doc10_blueprint.py",
    ),
    AcceptanceCriterion(
        "task_conformance",
        "Task-augmented tools/call returns immediate TaskData",
        "tests/test_mcp20_doc05_tasks.py",
    ),
    AcceptanceCriterion(
        "task_cancellation",
        "tasks/cancel marks cancelled; terminal cancel returns -32602",
        "tests/test_mcp20_doc05_tasks.py",
    ),
    AcceptanceCriterion(
        "ttl_safety",
        "Active tasks never evicted; terminal TTL from lastUpdatedAt",
        "tests/test_mcp20_doc06_task_store.py",
    ),
    AcceptanceCriterion(
        "multi_tenant",
        "Cross-tenant access returns TaskNotFoundError / -32602",
        "tests/test_mcp20_doc07_task_authorization.py",
    ),
    AcceptanceCriterion(
        "ssrf_security",
        "CIMD blocks special-use IPs and oversized payloads",
        "tests/test_mcp20_doc08_oauth_cimd.py",
    ),
    AcceptanceCriterion(
        "error_parity",
        "JSON-RPC codes match specification including SEP-2164",
        "tests/test_mcp20_doc02_jsonrpc_wire.py",
    ),
    AcceptanceCriterion(
        "automated_tests",
        "pytest suite covers all seven epics via doc00–doc11 modules",
        "tests/test_mcp20_doc11_epic_acceptance.py",
    ),
)

MCP20_SPEC_DOCS: tuple[str, ...] = (
    "00_OVERVIEW_AND_ARCHITECTURE.md",
    "01_STATELESS_HTTP_AND_LIFECYCLE.md",
    "02_JSONRPC_AND_WIRE_SPECIFICATION.md",
    "03_TOOL_RESOURCE_PROMPT_CONTRACTS.md",
    "04_MRTR_MULTI_ROUND_TRIP_SPEC.md",
    "05_MCP_TASKS_PROTOCOL_AND_LIFECYCLE.md",
    "06_TASK_STORE_AND_DISTRIBUTED_PERSISTENCE.md",
    "07_TASK_AUTHORIZATION_AND_TENANT_ISOLATION.md",
    "08_OAUTH21_CIMD_AND_SECURITY.md",
    "09_EXTENSIONS_CACHE_AND_OBSERVABILITY.md",
    "10_PYTHON_SDK_IMPLEMENTATION_BLUEPRINT.md",
    "TICKET_PLANE_PROJECT_MANAGEMENT.md",
)

MCP20_TEST_MODULES: tuple[str, ...] = tuple(
    sorted({d.test_module for d in EPIC_DELIVERABLES} | {c.test_module for c in ACCEPTANCE_CRITERIA})
)


def deliverables_for_epic(epic: ImplementationEpic) -> tuple[EpicDeliverable, ...]:
    return tuple(item for item in EPIC_DELIVERABLES if item.epic == epic)


def epic_coverage_complete() -> bool:
    """True when every epic has at least one mapped deliverable."""
    return all(deliverables_for_epic(epic) for epic in ImplementationEpic)


def acceptance_criteria_registered() -> bool:
    return len(ACCEPTANCE_CRITERIA) >= 8


def modern_protocol_target() -> str:
    return MODERN_PROTOCOL_VERSION


def iter_epic_summary() -> Iterable[str]:
    for epic in ImplementationEpic:
        items = deliverables_for_epic(epic)
        yield f"Epic {epic.value}: {len(items)} deliverable(s)"
