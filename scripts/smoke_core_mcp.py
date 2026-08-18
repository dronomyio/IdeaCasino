"""Smoke-test the read-only Core Intelligence facade without HTTP or external services."""
from __future__ import annotations

from app.core_intelligence import get_core_intelligence

core = get_core_intelligence()
catalog = core.catalog()
assert catalog["read_only"] is True
assert catalog["markets"] >= 1

idea = core.check_idea("MCP runtime security", geography="US", stage="pre-seed")
assert idea["subcategory"] == "MCP Security"
assert idea["data_tier"] == "demonstration_profile"
assert idea["geography"] == "US"

funding = core.search_funding(theme="MCP Security", days=90)
assert funding["provenance"] == "verified_source_facts"
assert isinstance(funding["records"], list)

comparison = core.compare_builder_vs_capital("MCP Security")
assert comparison["markets"]
assert comparison["markets"][0]["data_tier"] in {"demonstration_profile", "verified_metric_snapshot"}

underbuilt = core.find_underbuilt_markets(limit=5)
assert underbuilt["markets"]

pain = core.get_pain_graph("MCP Security", limit=3)
assert pain["pain_points"]

print("Core Intelligence smoke test passed")
