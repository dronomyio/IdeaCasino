"""Smoke test for the review-gated curated venture-investor collection universe."""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    config = json.loads((ROOT / "data" / "search_criteria.json").read_text())
    criterion = next(item for item in config["criteria"] if item["id"] == "curated_vc_activity")
    firms = criterion["vc_universe"]
    names = [firm["name"] for firm in firms]
    assert len(firms) == 25, f"Expected 25 curated firms, found {len(firms)}"
    assert len(set(names)) == len(names), "Curated investor firm names must be unique."
    assert {"Y Combinator", "Andreessen Horowitz", "Sequoia Capital", "Menlo Ventures", "DCVC"}.issubset(names)
    assert criterion["signal_type"] == "capital"
    assert {"funding_round", "investor_participation", "partner_participation"}.issubset(criterion["claim_types"])
    assert len(criterion["queries"]) == len(firms)
    assert "human review" in criterion["description"].lower()
    print("Curated VC universe smoke test passed.")


if __name__ == "__main__":
    main()
