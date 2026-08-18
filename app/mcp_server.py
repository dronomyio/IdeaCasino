"""Read-only Idea Casino MCP server.

Run via stdio with ``python -m app.mcp_server`` or ``docker compose run --rm -T mcp``.
Every tool delegates to the shared Core Intelligence facade; no tool may collect data,
call an LLM, enrich claims, validate claims, review claims, mutate Neo4j, or calculate
metrics.
"""
from __future__ import annotations

import json

from dotenv import load_dotenv
from mcp.server import MCPServer

from .core_intelligence import get_core_intelligence

load_dotenv()
mcp = MCPServer("Idea Casino MCP")


def _output(payload: dict) -> dict:
    """Return a structured, read-only payload suitable for any MCP host."""
    return payload


@mcp.tool()
def service_catalog() -> dict:
    """Describe the read-only Idea Casino service, data tiers, and fact/inference boundaries."""
    return _output(get_core_intelligence().catalog())


@mcp.tool()
def check_idea(idea: str, geography: str | None = None, stage: str | None = None, include_demo_profiles: bool = True) -> dict:
    """Check an idea against the stored market model and reviewed evidence.

    Returns the matched theme/subcategory, capital-builder-demand signals, classification,
    pain points, investor context, metric snapshots, evidence manifest, data tier, and
    explicit limitations. Geography and stage are query context unless source evidence
    includes those fields.
    """
    return _output(get_core_intelligence().check_idea(idea, geography, stage, include_demo_profiles))


@mcp.tool()
def explain_idea_odds(idea: str, include_demo_profiles: bool = True) -> dict:
    """Explain an idea's stored market-profile score components and evidence boundary.

    This is read-only and does not call external research. Set include_demo_profiles false
    to suppress profile-derived demonstration values.
    """
    return _output(get_core_intelligence().explain_idea_odds(idea, include_demo_profiles))


@mcp.tool()
def search_funding(theme: str | None = None, days: int = 365, limit: int = 50) -> dict:
    """Return reviewed funding-round source facts filtered by an optional theme and time window.

    It intentionally returns an empty record list rather than substituting demonstration
    funding figures when verified source-backed funding evidence is unavailable.
    """
    return _output(get_core_intelligence().search_funding(theme, days, limit))


@mcp.tool()
def get_investor_graph(company: str, include_inferences: bool = False) -> dict:
    """Return reviewed investor, partner, and funding relationships for a company.

    Observed facts are source-backed. Accepted model inferences are returned only when
    explicitly requested and remain labelled as model inference.
    """
    return _output(get_core_intelligence().get_investor_graph(company, include_inferences))


@mcp.tool()
def get_partner_activity(partner: str, days: int = 365) -> dict:
    """Return reviewed partner investment participation observed in the selected time window."""
    return _output(get_core_intelligence().get_partner_activity(partner, days))


@mcp.tool()
def compare_builder_vs_capital(theme: str, include_demo_profiles: bool = True) -> dict:
    """Compare capital and builder velocity for tracked markets matching a theme.

    Each item identifies whether it comes from a verified metric snapshot or a labelled
    demonstration profile. The tool does not mix the two tiers silently.
    """
    return _output(get_core_intelligence().compare_builder_vs_capital(theme, include_demo_profiles))


@mcp.tool()
def find_underbuilt_markets(limit: int = 10, include_demo_profiles: bool = True) -> dict:
    """List markets classified as Underbuilt, with their explicit data tier and classification rule."""
    return _output(get_core_intelligence().find_underbuilt_markets(limit, include_demo_profiles))


@mcp.tool()
def get_pain_graph(theme: str, limit: int = 10, include_demo_profiles: bool = True) -> dict:
    """Return pain-point opportunities for markets matching a theme with data-tier disclosure."""
    return _output(get_core_intelligence().get_pain_graph(theme, limit, include_demo_profiles))


@mcp.resource("ideacasino://catalog")
def catalog_resource() -> str:
    """Provide a compact capability and provenance catalog to MCP hosts."""
    return json.dumps(get_core_intelligence().catalog(), ensure_ascii=False, sort_keys=True, default=str)


if __name__ == "__main__":
    mcp.run()
