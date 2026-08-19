"""API contract smoke test for the SEC CIK resolver endpoint."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="idea-casino-cik-api-") as directory:
        root = Path(directory)
        os.environ["DATA_DIR"] = str(root)
        os.environ["EVIDENCE_FILE"] = str(root / "evidence.json")
        os.environ["CLAIMS_FILE"] = str(root / "claims.json")
        os.environ["COLLECTION_RUNS_FILE"] = str(root / "collection_runs.json")
        os.environ["DIRECT_API_RAW_FILE"] = str(root / "direct_api_raw.json")
        os.environ["SEC_USER_AGENT"] = "IdeaCasino test contact@example.com"
        from fastapi.testclient import TestClient
        from app.main import app
        from app.direct_collectors import DirectCollectors

        fixture = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}
        with patch.object(DirectCollectors, "_sec_get", return_value=fixture):
            client = TestClient(app)
            response = client.post("/api/sec-edgar/resolve-cik", json={"company": "Apple Inc.", "ticker": "AAPL"})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["selected"]["cik"] == "0000320193"
        assert payload["selected"]["match_kind"] == "exact_ticker"
    print("SEC CIK resolution API smoke test passed.")


if __name__ == "__main__":
    main()
