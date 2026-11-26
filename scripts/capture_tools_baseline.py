
"""Capture real tools/list responses from the unmodified base SDK."""
import asyncio
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import mcp.types as types

from nitrostack.testing import NitroTestingModule
from tests._baseline_catalog import ERAS, M

OUT = ROOT / "tests" / "fixtures" / "tools_list_baseline.json"


async def list_tools(era: str) -> str:
    harness = await NitroTestingModule.create(M, protocol_era=era)
    server = harness.app.mcp_server

    response = (
        await server.request_handlers[types.ListToolsRequest](
            types.ListToolsRequest(method="tools/list")
        )
    ).root

    return response.model_dump_json(
        by_alias=True,
        exclude_none=True,
    )


async def main() -> None:
    baseline = {}

    for era in ERAS:
        serialized = await list_tools(era)
        baseline[era] = serialized
        names = [
            item["name"]
            for item in json.loads(serialized)["tools"]
        ]
        print(f"{era}: {names}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(baseline, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    asyncio.run(main())
