"""Route smoke test for the product-facing reviewed-evidence tab APIs."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def main() -> None:
    client = TestClient(app)
    markets = client.get("/api/live-markets")
    assert markets.status_code == 200, markets.text
    payload = markets.json()
    assert payload["projection"] == "reviewed_evidence_live_market_table", payload
    first = payload["markets"][0]
    market_id = first["market_id"]
    assert "metrics" in first and "quality" in first

    flow = client.get(f"/api/live-markets/{market_id}/money-path")
    assert flow.status_code == 200, flow.text
    assert flow.json()["projection"] == "reviewed_evidence_live_money_path"

    pain = client.get(f"/api/live-markets/{market_id}/pain-graph")
    assert pain.status_code == 200, pain.text
    assert pain.json()["projection"] == "reviewed_evidence_live_pain_graph"

    graph = client.get("/api/live-market-graph")
    assert graph.status_code == 200, graph.text
    assert graph.json()["projection"] == "reviewed_evidence_live_market_graph"
    print("Reviewed-evidence tab API smoke test passed.")


if __name__ == "__main__":
    main()
