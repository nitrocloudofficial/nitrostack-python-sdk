import base64
import math
import re
from typing import Literal, Optional

from nitrostack import injectable, tool, widget, ExecutionContext
from pydantic import BaseModel, Field, field_validator


class CalculateInput(BaseModel):
    operation: Literal["add", "subtract", "multiply", "divide"] = Field(description="The operation to perform")
    a: float = Field(description="First number")
    b: float = Field(description="Second number")

    @field_validator("a", "b")
    @classmethod
    def _finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("must be a finite number")
        return value


class ConvertTemperatureInput(BaseModel):
    file_name: Optional[str] = Field(
        default=None,
        max_length=100,
        pattern=r"^[a-zA-Z0-9._-]+$",
        description="Optional input file name (alphanumeric, dot, underscore, hyphen)",
    )
    file_type: Optional[Literal["text/plain", "text/csv", "application/json"]] = Field(
        default=None,
        description="MIME type of optional uploaded file content",
    )
    file_content: Optional[str] = Field(
        default=None,
        max_length=2 * 1024 * 1024,
        description="Optional base64-encoded file payload (max 2MB)",
    )
    value: Optional[float] = Field(default=None, description="Temperature value to convert")
    from_unit: Optional[Literal["celsius", "fahrenheit", "kelvin"]] = Field(
        default=None, description="Unit to convert from",
    )
    to_unit: Optional[Literal["celsius", "fahrenheit", "kelvin"]] = Field(
        default=None, description="Unit to convert to",
    )

    @field_validator("value")
    @classmethod
    def _finite_value(cls, value: Optional[float]) -> Optional[float]:
        if value is not None and not math.isfinite(value):
            raise ValueError("must be a finite number")
        return value


class CalculateOutput(BaseModel):
    operation: str
    a: float
    b: float
    result: float
    expression: str


def _to_celsius(value: float, from_unit: str) -> float:
    if from_unit == "celsius":
        return value
    if from_unit == "fahrenheit":
        return (value - 32) * 5 / 9
    return value - 273.15


def _from_celsius(celsius: float, to_unit: str) -> float:
    if to_unit == "celsius":
        return celsius
    if to_unit == "fahrenheit":
        return (celsius * 9 / 5) + 32
    return celsius + 273.15


def _safe_file_name(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    base = name.split("/")[-1].split("\\")[-1]
    cleaned = re.sub(r"[^a-zA-Z0-9._-]", "_", base)
    return cleaned[:100] or None


def _decode_file_payload(content: str) -> str:
    data_url = re.match(r"^data:[A-Za-z0-9+/.-]+;base64,(.+)$", content)
    raw = data_url.group(1) if data_url else content
    return base64.b64decode(raw, validate=True).decode("utf-8", errors="replace")


@injectable()
class CalculatorTools:
    @tool(
        name="calculate",
        description="Perform basic arithmetic calculations",
        input_schema=CalculateInput,
        output_schema=CalculateOutput,
    )
    @widget("calculator-result")
    async def calculate(self, input: CalculateInput, context: ExecutionContext) -> dict:
        context.logger.info(f"Performing calculation: {input.operation} on {input.a} and {input.b}")

        result = 0.0
        symbol = ""

        if input.operation == "add":
            result = input.a + input.b
            symbol = "+"
        elif input.operation == "subtract":
            result = input.a - input.b
            symbol = "-"
        elif input.operation == "multiply":
            result = input.a * input.b
            symbol = "×"
        elif input.operation == "divide":
            if input.b == 0:
                raise ValueError("Cannot divide by zero")
            result = input.a / input.b
            symbol = "÷"

        return {
            "operation": input.operation,
            "a": input.a,
            "b": input.b,
            "result": result,
            "expression": f"{input.a} {symbol} {input.b} = {result}",
        }

    @tool(
        name="convert_temperature",
        title="Convert Temperature",
        description="Convert temperature from file content or direct input (celsius, fahrenheit, kelvin)",
        input_schema=ConvertTemperatureInput,
    )
    async def convert_temperature(self, input: ConvertTemperatureInput, context: ExecutionContext) -> dict:
        safe_name = _safe_file_name(input.file_name)
        context.logger.info(
            "Converting temperature",
            meta={
                "file_name": safe_name,
                "file_type": input.file_type,
                "value": input.value,
                "from_unit": input.from_unit,
                "to_unit": input.to_unit,
            },
        )

        value = input.value
        from_unit = input.from_unit
        to_unit = input.to_unit

        if input.file_content:
            try:
                decoded = _decode_file_payload(input.file_content)
                if value is None:
                    match = re.search(r"-?\d+(?:\.\d+)?", decoded)
                    if match:
                        value = float(match.group(0))
                context.logger.info(
                    "Processed file payload in-memory",
                    meta={"file_name": safe_name, "size_bytes": len(input.file_content)},
                )
            except (ValueError, UnicodeDecodeError) as exc:
                context.logger.warn(f"Failed to parse file content: {exc}")

        if value is None or from_unit is None or to_unit is None:
            raise ValueError("value, from_unit, and to_unit are required for conversion")

        celsius = _to_celsius(value, from_unit)
        result = round(_from_celsius(celsius, to_unit), 2)
        return {
            "result": result,
            "unit": to_unit,
            "original_value": value,
            "from_unit": from_unit,
            "to_unit": to_unit,
            "expression": f"{value}°{from_unit} = {result}°{to_unit}",
        }