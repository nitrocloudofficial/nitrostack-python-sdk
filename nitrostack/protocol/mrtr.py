"""Multi Round-Trip Request (MRTR) helpers — SEP-2322 (Doc 04)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

InputRequestKind = Literal["form", "url"]
RESULT_TYPE_INPUT_REQUIRED = "input_required"

DEFAULT_INPUT_REQUIRED_MESSAGE = "Additional input needed to complete this operation"


@dataclass
class InputRequest:
    """One elicitation prompt in an MRTR exchange (Doc 04 §3.1)."""

    id: str
    message: Optional[str] = None
    schema: Optional[dict[str, Any]] = None
    kind: InputRequestKind = "form"
    url: Optional[str] = None

    def to_wire_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"id": self.id, "kind": self.kind}
        if self.message is not None:
            payload["message"] = self.message
        if self.schema is not None:
            payload["schema"] = self.schema
        if self.url is not None:
            payload["url"] = self.url
        return payload


@dataclass
class InputRequiredResult:
    """Wire result pausing tool execution until the client supplies input (Doc 04 §3.2)."""

    input_requests: list[InputRequest]
    request_state: dict[str, Any]
    message: Optional[str] = None
    result_type: str = RESULT_TYPE_INPUT_REQUIRED

    def to_wire_dict(self) -> dict[str, Any]:
        return {
            "resultType": self.result_type,
            "message": self.message or DEFAULT_INPUT_REQUIRED_MESSAGE,
            "inputRequests": [req.to_wire_dict() for req in self.input_requests],
            "requestState": self.request_state,
        }


def accepted_content(input_responses: Optional[dict[str, Any]], request_id: str) -> Any:
    """
    Return the client's answer for ``request_id``, or ``None`` if not yet supplied.

    Handler primitive from Doc 04 §4.
    """
    if not input_responses or request_id not in input_responses:
        return None
    return input_responses[request_id]


def input_required(
    requests: list[InputRequest],
    request_state: dict[str, Any],
    message: Optional[str] = None,
) -> InputRequiredResult:
    """Halt tool execution and request additional client input (Doc 04 §4)."""
    if not requests:
        raise ValueError("input_required requires at least one InputRequest")
    return InputRequiredResult(
        input_requests=list(requests),
        request_state=dict(request_state),
        message=message,
    )


def split_mrtr_tool_params(params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], Optional[dict[str, Any]]]:
    """
    Extract MRTR resume fields from a ``tools/call`` params object.

    Returns ``(arguments, input_responses, request_state)``.
    """
    raw = dict(params or {})
    input_responses = raw.pop("inputResponses", None) or {}
    request_state = raw.pop("requestState", None)
    arguments = raw.pop("arguments", None) or {}

    if not isinstance(arguments, dict):
        arguments = {}
    if not isinstance(input_responses, dict):
        input_responses = {}

    # Clients may echo MRTR fields inside ``arguments`` on resume.
    if "inputResponses" in arguments:
        nested = arguments.pop("inputResponses") or {}
        if isinstance(nested, dict):
            input_responses = {**input_responses, **nested}
    if "requestState" in arguments:
        nested_state = arguments.pop("requestState")
        if isinstance(nested_state, dict):
            request_state = nested_state

    return arguments, input_responses, request_state


def split_mrtr_from_arguments(arguments: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], Optional[dict[str, Any]]]:
    """Extract MRTR resume fields when only the arguments dict is available."""
    return split_mrtr_tool_params({"arguments": arguments})


def is_input_required_result(value: Any) -> bool:
    """True when ``value`` is an InputRequiredResult or matching wire dict."""
    if isinstance(value, InputRequiredResult):
        return True
    return isinstance(value, dict) and value.get("resultType") == RESULT_TYPE_INPUT_REQUIRED


def coerce_input_required_result(value: Any) -> Optional[InputRequiredResult]:
    """Convert a handler return value or wire dict into InputRequiredResult."""
    if isinstance(value, InputRequiredResult):
        return value
    if not isinstance(value, dict) or value.get("resultType") != RESULT_TYPE_INPUT_REQUIRED:
        return None

    requests = []
    for item in value.get("inputRequests") or []:
        if not isinstance(item, dict) or "id" not in item:
            continue
        requests.append(
            InputRequest(
                id=item["id"],
                message=item.get("message"),
                schema=item.get("schema"),
                kind=item.get("kind", "form"),
                url=item.get("url"),
            )
        )
    return InputRequiredResult(
        input_requests=requests,
        request_state=dict(value.get("requestState") or {}),
        message=value.get("message"),
    )


def build_input_required_jsonrpc_result(request_id: Any, result: InputRequiredResult) -> dict[str, Any]:
    """Build a JSON-RPC success envelope for an MRTR pause response."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result.to_wire_dict(),
    }
