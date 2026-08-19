"""Smoke test for reviewed-evidence dashboard projection.

The test uses a temporary data directory. It verifies that a market becomes live only
when reviewed claims and all configured deterministic snapshots are present.
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
            {"id": "funding-1", "state": "verified", "evidence_type": "SOURCE_FACT", "claim_type": "funding_round", "observed_at": now, "payload": {"market_id": "mcp-security", "amount_usd": 2500000, "company": "Verified Co"}},
            {"id": "builder-1", "state": "manual_confirmed", "evidence_type": "SOURCE_FACT", "claim_type": "builder_project", "observed_at": now, "payload": {"market_id": "mcp-security", "project_url": "https://example.test/verified-project"}},
            {"id": "demand-1", "state": "verified", "evidence_type": "SOURCE_FACT", "claim_type": "customer_adoption", "observed_at": now, "payload": {"market_id": "mcp-security", "company": "Verified Buyer"}},
        ]
        snapshots = [
            {"id": f"metric-{metric}", "market_id": "mcp-security", "metric": metric, "status": "computed", "value": value, "calculated_at": now, "input_ids": [row["id"] for row in claims]}
            for metric, value in (("capital_deployed", 2500000), ("round_count", 1), ("active_builders", 1), ("buyer_demand", 0.7), ("capital_velocity", 0.2), ("opportunity_score", 42))
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
        from app.live_projection import LiveMarketProjection

        projection = LiveMarketProjection().dashboard()
        row = next(item for item in projection["markets"] if item["market_id"] == "mcp-security")
        assert row["quality"] == "verified_live", row
        assert row["metrics"]["capital_deployed_usd"] == 2500000
        assert projection["summary"]["verified_live_markets"] == 1

        write_json(data / "claims.json", [])
        gap_projection = LiveMarketProjection().dashboard()
        gap_row = next(item for item in gap_projection["markets"] if item["market_id"] == "mcp-security")
        assert gap_row["quality"] == "insufficient_evidence", gap_row
        assert gap_row["metrics"]["capital_deployed_usd"] is None
        print("Live market projection smoke test passed.")


if __name__ == "__main__":
    main()
