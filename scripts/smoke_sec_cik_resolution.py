"""Smoke test for conservative SEC company-name and ticker CIK resolution."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from app.direct_collectors import DirectCollectors


class FixtureCollectors(DirectCollectors):
    def _sec_get(self, url: str):  # type: ignore[override]
        return {
            "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
            "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corporation"},
            "2": {"cik_str": 1652044, "ticker": "GOOGL", "title": "Alphabet Inc."},
        }


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="idea-casino-cik-") as directory:
        root = Path(directory)
        os.environ["DATA_DIR"] = str(root)
        os.environ["EVIDENCE_FILE"] = str(root / "evidence.json")
        os.environ["CLAIMS_FILE"] = str(root / "claims.json")
        os.environ["COLLECTION_RUNS_FILE"] = str(root / "collection_runs.json")
        os.environ["DIRECT_API_RAW_FILE"] = str(root / "direct_api_raw.json")
        os.environ["DIRECT_API_CRITERIA_FILE"] = "/home/ubuntu/idea_casino/github_repo/data/direct_api_criteria.json"
        collector = FixtureCollectors()
        ticker = collector.resolve_cik("", "aapl")
        assert ticker["selected"]["cik"] == "0000320193"
        assert ticker["selected"]["match_kind"] == "exact_ticker"
        exact = collector.resolve_cik("Microsoft Corporation")
        assert exact["selected"]["cik"] == "0000789019"
        partial = collector.resolve_cik("Apple")
        assert partial["selected"] is None and partial["requires_confirmation"]
        target, resolution = collector._resolve_sec_target({"company": "Apple Inc.", "ticker": "AAPL", "market_id": "mcp-security"})
        assert target["cik"] == "0000320193" and resolution is not None
    print("SEC CIK resolution smoke test passed.")


if __name__ == "__main__":
    main()
