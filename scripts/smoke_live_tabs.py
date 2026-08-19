"""Smoke test for strict reviewed-evidence tab projections.

The fixture has enough reviewed claims and computed snapshots for exactly one market.
The test verifies that table, money, pain, and graph payloads never pull numeric
values from the legacy seeded market profiles.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value))


def main() -> None:
    with tempfile.TemporaryDirectory() as raw:
        data = Path(raw)
        shutil.copy(PROJECT / "data" / "market_intelligence.json", data / "market_intelligence.json")
        now = datetime.now(timezone.utc).isoformat()
        claims = [
            {"id": "funding-1", "state": "verified", "evidence_type": "SOURCE_FACT", "claim_type": "funding_round", "observed_at": now, "payload": {"market_id": "mcp-security", "amount_usd": 2_500_000, "company": "Verified Co"}},
            {"id": "builder-1", "state": "manual_confirmed", "evidence_type": "SOURCE_FACT", "claim_type": "builder_project", "observed_at": now, "payload": {"market_id": "mcp-security", "project_url": "https://example.test/verified-project"}},
            {"id": "demand-1", "state": "verified", "evidence_type": "SOURCE_FACT", "claim_type": "customer_adoption", "observed_at": now, "payload": {"market_id": "mcp-security", "company": "Verified Buyer"}},
        ]
        base = {"market_id": "mcp-security", "status": "computed", "calculated_at": now, "input_ids": [row["id"] for row in claims]}
        snapshots = [
            {**base, "id": f"metric-{metric}", "metric": metric, "value": value, "detail": detail}
            for metric, value, detail in (
                ("capital_deployed", 2_500_000, {}),
                ("round_count", 1, {}),
                ("active_builders", 1, {}),
                ("buyer_demand", 0.7, {}),
                ("capital_velocity", 0.2, {}),
                ("opportunity_score", 42, {}),
                ("downstream_dollar::security_testing", 800_000, {"category": "security testing"}),
                ("pain_density::policy_drift", 250_000, {"pain_point": "policy drift"}),
                ("pain_opportunity::policy_drift", 31, {"pain_point": "policy drift"}),
            )
        ]
        write_json(data / "claims.json", claims)
        write_json(data / "evidence.json", [])
        write_json(data / "collection_runs.json", [])
        write_json(data / "metric_snapshots.json", snapshots)
        write_json(data / "calculation_definitions.json", {"version": "test", "metrics": {}})
        os.environ.update({
            "DATA_DIR": str(data),
            "MARKET_INTELLIGENCE_FILE": str(data / "market_intelligence.json"),
            "CLAIMS_FILE": str(data / "claims.json"),
            "EVIDENCE_FILE": str(data / "evidence.json"),
            "COLLECTION_RUNS_FILE": str(data / "collection_runs.json"),
            "METRIC_SNAPSHOTS_FILE": str(data / "metric_snapshots.json"),
            "CALCULATION_DEFINITIONS_FILE": str(data / "calculation_definitions.json"),
            "LIVE_PROJECTION_MIN_VERIFIED_CLAIMS": "3",
            "LIVE_PROJECTION_MIN_SIGNAL_TYPES": "2",
        })
        from app.live_tabs import LiveTabProjection

        tabs = LiveTabProjection()
        markets = tabs.markets()
        row = next(item for item in markets["markets"] if item["market_id"] == "mcp-security")
        assert row["quality"] == "verified_live", row
        assert row["metrics"]["capital_deployed_usd"] == 2_500_000
        assert all(item["metrics"]["capital_deployed_usd"] is None for item in markets["markets"] if item["market_id"] != "mcp-security")

        flow = tabs.money_path("mcp-security")
        assert flow and flow["downstream_dollars"][0]["value_usd"] == 800_000, flow
        assert flow["downstream_dollars"][0]["category"] == "security testing"

        pain = tabs.pain_graph("mcp-security")
        assert pain and pain["pain_points"][0]["opportunity_score"] == 31, pain
        assert pain["pain_points"][0]["pain_density_usd"] == 250_000

        graph = tabs.market_graph()
        assert graph["projection"] == "reviewed_evidence_live_market_graph"
        assert any(node["id"] == "signal:mcp-security:capital_deployed_usd" for node in graph["nodes"])
        assert "excluded" in graph["note"].lower()

        write_json(data / "claims.json", [])
        gaps = LiveTabProjection().markets()
        assert all(item["quality"] == "insufficient_evidence" for item in gaps["markets"])
        assert all(item["metrics"]["opportunity_score"] is None for item in gaps["markets"])
        print("Reviewed-evidence tab projection smoke test passed.")


if __name__ == "__main__":
    main()
