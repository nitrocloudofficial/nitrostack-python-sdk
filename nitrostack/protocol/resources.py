"""Resource URI resolution for static resources and templates."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Pattern, Sequence

_TEMPLATE_PARAM_RE = re.compile(r"\{([^}]+)\}")


def extract_template_param_names(uri_template: str) -> list[str]:
    """Extract `{param}` names from a URI template."""
    return _TEMPLATE_PARAM_RE.findall(uri_template)


def uri_template_to_pattern(uri_template: str) -> Pattern[str]:
    """
    Compile a URI template to a regex with named groups.

    Example: ``mcp://customers/{customerId}/orders`` matches concrete URIs and
    captures ``customerId``.
    """
    param_names = extract_template_param_names(uri_template)
    regex_str = re.escape(uri_template)
    for pname in param_names:
        regex_str = regex_str.replace(re.escape("{" + pname + "}"), f"(?P<{pname}>[^/]+)")
    return re.compile(f"^{regex_str}$")


@dataclass(frozen=True)
class ResourceMatch:
    """Result of resolving a resource URI against the registry."""

    entry: Any
    path_params: dict[str, str]
    matched_via_template: bool = False


def resolve_resource_uri(
    uri: str,
    static_resources: Mapping[str, Any],
    templates: Sequence[tuple[Pattern[str], Any]],
) -> Optional[ResourceMatch]:
    """
    Resolve a resource URI:
    1. Exact static map lookup
    2. URI template pattern match
    """
    static_entry = static_resources.get(uri)
    if static_entry is not None:
        return ResourceMatch(entry=static_entry, path_params={})

    for pattern, entry in templates:
        match = pattern.match(uri)
        if match:
            return ResourceMatch(
                entry=entry,
                path_params=match.groupdict(),
                matched_via_template=True,
            )

    return None
