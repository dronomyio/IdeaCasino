"""Smoke-test the Idea Casino MCP server without a network transport."""
from __future__ import annotations

import asyncio
import json

from mcp import Client

from app.mcp_server import mcp


def payload(result):
    """Decode the portable JSON text result used by hosts that do not expose structured_content."""
    if result.structured_content:
        return result.structured_content.get("result", result.structured_content)
    return json.loads(result.content[0].text)


async def main() -> None:
    async with Client(mcp) as client:
        catalog = payload(await client.call_tool("service_catalog", {}))
        assert catalog["read_only"] is True

        idea = payload(await client.call_tool("check_idea", {"idea": "MCP runtime security", "geography": "US", "stage": "pre-seed"}))
        assert idea["subcategory"] == "MCP Security"
        assert idea["data_tier"] == "demonstration_profile"

        funding = payload(await client.call_tool("search_funding", {"theme": "MCP Security", "days": 90}))
        assert funding["provenance"] == "verified_source_facts"

        underbuilt = payload(await client.call_tool("find_underbuilt_markets", {"limit": 5}))
        assert underbuilt["markets"]

        pain = payload(await client.call_tool("get_pain_graph", {"theme": "MCP Security", "limit": 3}))
        assert pain["pain_points"]

    print("MCP smoke test passed: read-only catalog, idea, funding, underbuilt-market, and pain-graph tools returned structured provenance-aware responses")


if __name__ == "__main__":
    asyncio.run(main())
