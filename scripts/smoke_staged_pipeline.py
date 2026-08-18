"""In-repository smoke test for the normalize → enrich → validate contract.

This test does not call external services or an LLM. It verifies the deterministic
normalizer and validator enforce the required source-fact/inference separation.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from app.normalizer import normalize_document
from app.validator import CandidateValidator

ROOT = Path(__file__).resolve().parents[1]

with tempfile.TemporaryDirectory() as directory:
    data = Path(directory)
    taxonomy = ROOT / "data" / "ontology_taxonomy.json"
    (data / "ontology_taxonomy.json").write_text(taxonomy.read_text())
    evidence = normalize_document(
        {
            "url": "https://example.test/acme?utm_source=test",
            "title": "Acme Robotics raises $22 million",
            "content": "Acme Robotics raised $22 million in a Series A led by XYZ Ventures. The company uses computer vision.",
            "published_date": "Aug. 14, 2026",
        },
        source_type="news", criterion_id="funding_rounds", run_id="smoke-run",
    )
    evidence["id"] = "evidence-1"
    claims = [{
        "id": "claim-1", "evidence_id": "evidence-1", "claim_type": "funding_round", "evidence_type": "SOURCE_FACT",
        "state": "pending_validation", "payload": {"company": "Acme Robotics", "amount_usd": 22000000, "stage": "Series A", "theme": "AI", "subcategory": "Physical AI", "application": "Warehouse automation"},
    }, {
        "id": "claim-2", "evidence_id": "evidence-1", "claim_type": "ontology_relationship", "evidence_type": "MODEL_INFERENCE",
        "state": "pending_validation", "payload": {"company": "Acme Robotics", "relationship": "LIKELY_NEEDS", "target": "Simulation", "rationale": "Robotics development requires testing environments."},
    }]
    (data / "evidence.json").write_text(json.dumps([evidence]))
    (data / "claims.json").write_text(json.dumps(claims))
    os.environ.update({
        "DATA_DIR": str(data), "EVIDENCE_FILE": str(data / "evidence.json"), "CLAIMS_FILE": str(data / "claims.json"),
        "ONTOLOGY_TAXONOMY_FILE": str(data / "ontology_taxonomy.json"),
    })
    report = CandidateValidator().validate_pending()
    rows = {row["id"]: row for row in json.loads((data / "claims.json").read_text())}
    assert evidence["canonical_url"] == "https://example.test/acme"
    assert evidence["literal_mentions"]["amounts_usd"][0]["amount_usd"] == 22000000
    assert rows["claim-1"]["validation"]["status"] == "passed"
    assert rows["claim-1"]["validation"]["trusted_graph_eligible"] is True
    assert rows["claim-2"]["validation"]["status"] == "inference_only"
    assert rows["claim-2"]["validation"]["trusted_graph_eligible"] is False
    assert report["validated"] == 2

print("Staged pipeline smoke test passed")
