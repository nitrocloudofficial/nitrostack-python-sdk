"""Tests for MCP 2.0 MRTR multi round-trip requests."""

import asyncio
import os
import sys

from pydantic import BaseModel, Field

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nitrostack import injectable
from nitrostack.core.app import McpApplicationFactory, ServerConfig, mcp_app, parse_tool_input
from nitrostack.core.context import ExecutionContext
from nitrostack.core.decorators import tool
from nitrostack.core.di import DIContainer
from nitrostack.core.module import module
from nitrostack.protocol.mrtr import (
    InputRequest,
    accepted_content,
    build_input_required_jsonrpc_result,
    input_required,
    split_mrtr_tool_params,
)


class TransferInput(BaseModel):
    amount: float = Field(description="Amount to transfer")


def setup_function() -> None:
    DIContainer.reset()


def teardown_function() -> None:
    DIContainer.reset()


class TestMrtrHelpers:
    def test_accepted_content_returns_none_when_missing(self):
        assert accepted_content(None, "confirm") is None
        assert accepted_content({}, "confirm") is None

    def test_accepted_content_returns_value(self):
        assert accepted_content({"confirm": "DELETE"}, "confirm") == "DELETE"

    def test_input_required_wire_shape(self):
        result = input_required(
            requests=[
                InputRequest(
                    id="otp_code",
                    message="Enter 6-digit code",
                    schema={"type": "string", "pattern": "^[0-9]{6}$"},
                )
            ],
            request_state={"transactionId": "tx_89712398", "stage": "awaiting_2fa"},
        )
        wire = result.to_wire_dict()
        assert wire["resultType"] == "input_required"
        assert wire["inputRequests"][0]["id"] == "otp_code"
        assert wire["requestState"]["transactionId"] == "tx_89712398"

    def test_split_mrtr_tool_params(self):
        params = {
            "name": "transfer_funds",
            "arguments": {"recipient": "alice@example.com", "amount": 500},
            "inputResponses": {"otp_code": "481920"},
            "requestState": {"transactionId": "tx_89712398", "stage": "awaiting_2fa"},
        }
        arguments, responses, state = split_mrtr_tool_params(params)
        assert arguments["amount"] == 500
        assert responses["otp_code"] == "481920"
        assert state["stage"] == "awaiting_2fa"

    def test_jsonrpc_envelope(self):
        result = input_required(
            requests=[InputRequest(id="confirm_delete", message="Type DELETE")],
            request_state={"step": 1, "db": "prod_users"},
        )
        envelope = build_input_required_jsonrpc_result("req-99", result)
        assert envelope["id"] == "req-99"
        assert envelope["result"]["resultType"] == "input_required"


class TestMrtrToolExecution:
    def test_tool_returns_input_required_result(self):
        @injectable()
        class MrtrController:
            @tool(name="delete_database", description="delete", input_schema=TransferInput)
            async def delete_database(self, input: TransferInput, context: ExecutionContext):
                confirmation = accepted_content(context.input_responses, "confirm_delete")
                if confirmation is None:
                    return input_required(
                        requests=[
                            InputRequest(
                                id="confirm_delete",
                                message="Type DELETE to confirm",
                                schema={"type": "string"},
                            )
                        ],
                        request_state={"step": 1, "db": "prod_users"},
                    )
                if confirmation == "DELETE":
                    return {"status": "dropped", "db": "prod_users"}
                return {"status": "cancelled"}

        @module(name="MrtrModule", controllers=[MrtrController])
        class MrtrModule:
            pass

        @mcp_app(module=MrtrModule, server=ServerConfig(name="mrtr"))
        class MrtrApp:
            pass

        async def _run():
            app = await McpApplicationFactory.create(MrtrApp)

            first = await app._call_tool("delete_database", {"amount": 0})
            assert first.structuredContent["resultType"] == "input_required"
            assert first.structuredContent["inputRequests"][0]["id"] == "confirm_delete"

            second = await app._call_tool(
                "delete_database",
                {
                    "amount": 0,
                    "inputResponses": {"confirm_delete": "DELETE"},
                    "requestState": {"step": 1, "db": "prod_users"},
                },
            )
            assert second.structuredContent["status"] == "dropped"

        asyncio.run(_run())

    def test_split_mrtr_preserves_tool_arguments_for_validation(self):
        arguments, responses, state = split_mrtr_tool_params(
            {
                "arguments": {
                    "amount": 12.5,
                    "inputResponses": {"confirm": True},
                    "requestState": {"stage": 2},
                }
            }
        )
        model = parse_tool_input(TransferInput, arguments)
        assert model.amount == 12.5
        assert responses["confirm"] is True
        assert state["stage"] == 2
