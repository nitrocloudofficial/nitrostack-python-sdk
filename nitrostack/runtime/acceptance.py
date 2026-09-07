"""MCP 2026-07-28 feature-area and acceptance-criteria registry."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Iterable

from nitrostack.protocol.version import MODERN_PROTOCOL_VERSION


class ProtocolArea(IntEnum):
    """Major MCP 2026-07-28 implementation areas."""

    CORE_JSONRPC = 1
    STATELESS_HTTP = 2
    REGISTRIES_SCHEMA = 3
    ASYNC_TASKS = 4
    MULTI_TENANT = 5
    OAUTH_CIMD = 6
    MRTR_OBSERVABILITY = 7


@dataclass(frozen=True)
class ProtocolDeliverable:
    area: ProtocolArea
    item: str
    test_module: str


@dataclass(frozen=True)
class AcceptanceCriterion:
    key: str
    description: str
    test_module: str


PROTOCOL_DELIVERABLES: tuple[ProtocolDeliverable, ...] = (
    ProtocolDeliverable(
        ProtocolArea.CORE_JSONRPC,
        "JSON-RPC error code hierarchy (-32700..-32603, -32020)",
        "tests/test_mcp20_jsonrpc_wire.py",
    ),
    ProtocolDeliverable(
        ProtocolArea.CORE_JSONRPC,
        "SEP-2164 missing resource returns -32602",
        "tests/test_mcp20_contracts.py",
    ),
    ProtocolDeliverable(
        ProtocolArea.CORE_JSONRPC,
        "Reject tasks/result and tasks/list on modern wire",
        "tests/test_mcp20_tasks.py",
    ),
    ProtocolDeliverable(
        ProtocolArea.STATELESS_HTTP,
        "POST /mcp stateless ingress without Mcp-Session-Id",
        "tests/test_mcp20_stateless_http.py",
    ),
    ProtocolDeliverable(
        ProtocolArea.STATELESS_HTTP,
        "server/discover and ping fast path",
        "tests/test_mcp20_stateless_http.py",
    ),
    ProtocolDeliverable(
        ProtocolArea.STATELESS_HTTP,
        "CORS headers for MCP preflight",
        "tests/test_mcp20_stateless_http.py",
    ),
    ProtocolDeliverable(
        ProtocolArea.REGISTRIES_SCHEMA,
        "JSON Schema 2020-12 with depth bounding (max 64)",
        "tests/test_mcp20_contracts.py",
    ),
    ProtocolDeliverable(
        ProtocolArea.REGISTRIES_SCHEMA,
        "Resource URI templates and resolution",
        "tests/test_mcp20_contracts.py",
    ),
    ProtocolDeliverable(
        ProtocolArea.ASYNC_TASKS,
        "Task state machine and tasks/get embedded result",
        "tests/test_mcp20_tasks.py",
    ),
    ProtocolDeliverable(
        ProtocolArea.ASYNC_TASKS,
        "Pluggable TaskStore and terminal-only TTL eviction",
        "tests/test_mcp20_task_store.py",
    ),
    ProtocolDeliverable(
        ProtocolArea.MULTI_TENANT,
        "TaskAccessContext isolation and anti-enumeration",
        "tests/test_mcp20_task_authorization.py",
    ),
    ProtocolDeliverable(
        ProtocolArea.OAUTH_CIMD,
        "CIMD resolver with SSRF defenses and RFC 9207 iss",
        "tests/test_mcp20_oauth_cimd.py",
    ),
    ProtocolDeliverable(
        ProtocolArea.MRTR_OBSERVABILITY,
        "MRTR input_required elicitation helpers",
        "tests/test_mcp20_mrtr.py",
    ),
    ProtocolDeliverable(
        ProtocolArea.MRTR_OBSERVABILITY,
        "Extensions map, trace context, and cache hints",
        "tests/test_mcp20_extensions_cache_observability.py",
    ),
)

ACCEPTANCE_CRITERIA: tuple[AcceptanceCriterion, ...] = (
    AcceptanceCriterion(
        "stateless",
        "No Mcp-Session-Id emitted; stateless POST /mcp works",
        "tests/test_mcp20_blueprint.py",
    ),
    AcceptanceCriterion(
        "task_conformance",
        "Task-augmented tools/call returns immediate TaskData",
        "tests/test_mcp20_tasks.py",
    ),
    AcceptanceCriterion(
        "task_cancellation",
        "tasks/cancel marks cancelled; terminal cancel returns -32602",
        "tests/test_mcp20_tasks.py",
    ),
    AcceptanceCriterion(
        "ttl_safety",
        "Active tasks never evicted; terminal TTL from lastUpdatedAt",
        "tests/test_mcp20_task_store.py",
    ),
    AcceptanceCriterion(
        "multi_tenant",
        "Cross-tenant access returns TaskNotFoundError / -32602",
        "tests/test_mcp20_task_authorization.py",
    ),
    AcceptanceCriterion(
        "ssrf_security",
        "CIMD blocks special-use IPs and oversized payloads",
        "tests/test_mcp20_oauth_cimd.py",
    ),
    AcceptanceCriterion(
        "error_parity",
        "JSON-RPC codes match specification including SEP-2164",
        "tests/test_mcp20_jsonrpc_wire.py",
    ),
    AcceptanceCriterion(
        "automated_tests",
        "pytest suite covers all seven protocol areas",
        "tests/test_mcp20_acceptance.py",
    ),
)

MCP20_TEST_MODULES: tuple[str, ...] = tuple(
    sorted({d.test_module for d in PROTOCOL_DELIVERABLES} | {c.test_module for c in ACCEPTANCE_CRITERIA})
)


def deliverables_for_area(area: ProtocolArea) -> tuple[ProtocolDeliverable, ...]:
    return tuple(item for item in PROTOCOL_DELIVERABLES if item.area == area)


def protocol_coverage_complete() -> bool:
    """True when every protocol area has at least one mapped deliverable."""
    return all(deliverables_for_area(area) for area in ProtocolArea)


def acceptance_criteria_registered() -> bool:
    return len(ACCEPTANCE_CRITERIA) >= 8


def modern_protocol_target() -> str:
    return MODERN_PROTOCOL_VERSION


def iter_area_summary() -> Iterable[str]:
    for area in ProtocolArea:
        items = deliverables_for_area(area)
        yield f"Area {area.value}: {len(items)} deliverable(s)"
