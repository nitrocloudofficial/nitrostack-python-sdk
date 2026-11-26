"""Starter calculator input limits (TS #368)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

import importlib.util
import os

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_PATH = os.path.join(_ROOT, "nitrostack", "templates", "starter", "modules", "calculator", "calculator_tools.py")
_spec = importlib.util.spec_from_file_location("starter_calc_hardening", _PATH)
_mod = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(_mod)
CalculateInput = _mod.CalculateInput
ConvertTemperatureInput = _mod.ConvertTemperatureInput


def test_calculate_rejects_non_finite_operands():
    with pytest.raises(ValidationError):
        CalculateInput(operation="add", a=float("inf"), b=1.0)


def test_convert_temperature_rejects_invalid_filename():
    with pytest.raises(ValidationError):
        ConvertTemperatureInput(file_name="../evil.txt", value=20.0, from_unit="celsius", to_unit="fahrenheit")


def test_convert_temperature_accepts_safe_optional_file_fields():
    model = ConvertTemperatureInput(
        file_name="reading.csv",
        file_type="text/csv",
        file_content="MjU=",
        value=25.0,
        from_unit="celsius",
        to_unit="fahrenheit",
    )
    assert model.file_name == "reading.csv"