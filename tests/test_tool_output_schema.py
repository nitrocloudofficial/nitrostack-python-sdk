from typing import Any, Dict, Optional

import jsonschema
from pydantic import BaseModel

from nitrostack.core.app import inspector_friendly_schema, tool_json_schema


class Out(BaseModel):
    maybe: Optional[str] = None
    n: Optional[int] = None
    required_str: str = "x"


def _accepts_null(prop: Dict[str, Any]) -> bool:
    for key in ("anyOf", "oneOf"):
        if any(v.get("type") == "null" for v in prop.get(key, [])):
            return True
    types = prop.get("type")
    return isinstance(types, list) and "null" in types


def test_output_schema_keeps_null_for_model():
    props = tool_json_schema(Out)["properties"]
    assert _accepts_null(props["maybe"])
    assert _accepts_null(props["n"])


def test_output_schema_keeps_null_for_dict():
    props = tool_json_schema(Out.model_json_schema())["properties"]
    assert _accepts_null(props["maybe"])
    assert _accepts_null(props["n"])


def test_output_schema_does_not_loosen_required_fields():
    props = tool_json_schema(Out)["properties"]
    assert not _accepts_null(props["required_str"])
    assert props["required_str"]["type"] == "string"


def test_output_schema_validates_null_payload():
    jsonschema.validate({"maybe": None, "n": None, "required_str": "x"}, tool_json_schema(Out))


def test_input_schema_still_unwraps_optional_for_inspector():
    props = inspector_friendly_schema(Out.model_json_schema())["properties"]
    assert not _accepts_null(props["maybe"])
    assert props["maybe"]["type"] == "string"
    assert props["n"]["type"] == "integer"