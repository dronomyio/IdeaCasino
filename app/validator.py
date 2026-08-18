"""Deterministic validate.py stage for Idea Casino.

Validation checks source-supported candidates against normalized literal evidence and the
controlled ontology. It never upgrades a candidate to a production fact: successful
source-fact candidates remain review_required until a human verifies them.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .evidence_engine import JsonList, stable_id, utc_now

PROJECT_DATA = Path(__file__).resolve().parents[1] / "data"


class CandidateValidator:
    def __init__(self):
        configured_data = Path(os.getenv("DATA_DIR", "/app/data"))
        self.data_dir = configured_data if configured_data.exists() else PROJECT_DATA
        self.evidence = JsonList(Path(os.getenv("EVIDENCE_FILE", str(self.data_dir / "evidence.json"))))
        self.claims = JsonList(Path(os.getenv("CLAIMS_FILE", str(self.data_dir / "claims.json"))))
        taxonomy_path = Path(os.getenv("ONTOLOGY_TAXONOMY_FILE", str(self.data_dir / "ontology_taxonomy.json")))
        if not taxonomy_path.exists():
            taxonomy_path = PROJECT_DATA / "ontology_taxonomy.json"
        self.taxonomy = json.loads(taxonomy_path.read_text())

    def _taxonomy_sets(self) -> dict[str, set[str]]:
        other = self.taxonomy["other"]
        themes = set(self.taxonomy["themes"])
        subcategories = {subcategory for theme in self.taxonomy["themes"].values() for subcategory in theme["subcategories"]}
        applications = {application for theme in self.taxonomy["themes"].values() for items in theme["subcategories"].values() for application in items}
        return {
            "theme": themes | {other}, "subcategory": subcategories | {other}, "application": applications | {other},
            "technology": set(self.taxonomy["technologies"]) | {other}, "pain": set(self.taxonomy["pain_points"]) | {other},
            "buyer": set(self.taxonomy["buyer_types"]) | {other}, "dependency": set(self.taxonomy["dependencies"]) | {other},
            "stage": set(self.taxonomy["stages"]), "source_relationship": set(self.taxonomy["relationship_types"]["source_fact"]),
            "inference_relationship": set(self.taxonomy["relationship_types"]["model_inference"]),
        }

    @staticmethod
    def _text(evidence: dict[str, Any]) -> str:
        return f"{evidence.get('title', '')}\n{evidence.get('text') or evidence.get('content_excerpt', '')}".lower()

    @staticmethod
    def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
        return {"name": name, "passed": passed, "detail": detail}

    def _taxonomy_checks(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        allowed = self._taxonomy_sets()
        checks: list[dict[str, Any]] = []
        for key, mapping in (("theme", "theme"), ("subcategory", "subcategory"), ("application", "application")):
            value = payload.get(key)
            if value is not None:
                checks.append(self._check(f"ontology_{key}", value in allowed[mapping], f"{value!r} is {'allowed' if value in allowed[mapping] else 'not in'} the controlled taxonomy."))
        relationship = payload.get("relationship")
        if relationship:
            relation_set = allowed["source_relationship"] | allowed["inference_relationship"]
            checks.append(self._check("ontology_relationship", relationship in relation_set, f"{relationship!r} is {'allowed' if relationship in relation_set else 'not allowed'} by the relationship taxonomy."))
        target = payload.get("target")
        if relationship in {"LIKELY_NEEDS", "LIKELY_DEPENDS_ON"} and target:
            checks.append(self._check("ontology_inference_target", target in allowed["dependency"], f"{target!r} is {'an allowed' if target in allowed['dependency'] else 'not an allowed'} dependency."))
        if payload.get("stage"):
            checks.append(self._check("stage_vocabulary", payload["stage"] in allowed["stage"], f"{payload['stage']!r} is {'a valid' if payload['stage'] in allowed['stage'] else 'not a valid'} stage."))
        return checks

    def _duplicate(self, claim: dict[str, Any], all_claims: list[dict[str, Any]]) -> bool:
        if claim.get("claim_type") != "funding_round":
            return False
        payload = claim.get("payload") or {}
        fingerprint = stable_id(payload.get("company"), payload.get("announced_date"), payload.get("stage"), payload.get("amount_usd"))
        for other in all_claims:
            if other.get("id") == claim.get("id") or other.get("claim_type") != "funding_round":
                continue
            other_payload = other.get("payload") or {}
            other_fingerprint = stable_id(other_payload.get("company"), other_payload.get("announced_date"), other_payload.get("stage"), other_payload.get("amount_usd"))
            if other_fingerprint == fingerprint and other.get("state") not in {"rejected"}:
                return True
        return False

    def validate(self, claim: dict[str, Any], evidence: dict[str, Any], all_claims: list[dict[str, Any]]) -> dict[str, Any]:
        payload = claim.get("payload") or {}
        text = self._text(evidence)
        checks: list[dict[str, Any]] = [
            self._check("source_url", bool(evidence.get("url")), "Canonical source URL is present."),
            self._check("source_date", bool(evidence.get("published_date") or evidence.get("retrieved_at")), "Publication or retrieval date is present."),
        ]
        checks.extend(self._taxonomy_checks(payload))
        evidence_type = claim.get("evidence_type", "SOURCE_FACT")
        company = payload.get("company")
        if company:
            checks.append(self._check("company_literal", company.lower() in text, f"Company {company!r} {'appears' if company.lower() in text else 'does not appear'} in source text."))
        if claim.get("claim_type") == "funding_round":
            amount = payload.get("amount_usd")
            mentions = [item.get("amount_usd") for item in (evidence.get("literal_mentions") or {}).get("amounts_usd", [])]
            checks.append(self._check("funding_amount_literal", amount is None or amount in mentions, f"Amount {amount!r} {'matches' if amount is None or amount in mentions else 'does not match'} normalized source amount mentions {mentions}."))
            stage = payload.get("stage")
            stages = [item.get("stage") for item in (evidence.get("literal_mentions") or {}).get("stages", [])]
            checks.append(self._check("funding_stage_literal", not stage or stage in stages, f"Stage {stage!r} {'matches' if not stage or stage in stages else 'does not match'} normalized source stage mentions {stages}."))
            checks.append(self._check("funding_amount_plausible", amount is None or 1_000 <= float(amount) <= 100_000_000_000, "Funding amount is within configured plausible bounds."))
            checks.append(self._check("duplicate_event", not self._duplicate(claim, all_claims), "No duplicate funding event fingerprint exists."))
        if claim.get("claim_type") in {"investor_participation", "partner_participation"}:
            firm = payload.get("firm")
            if firm:
                checks.append(self._check("investor_literal", firm.lower() in text, f"Investor {firm!r} {'appears' if firm.lower() in text else 'does not appear'} in source text."))
            partner = payload.get("partner")
            if partner:
                checks.append(self._check("partner_literal", partner.lower() in text, f"Partner {partner!r} {'appears' if partner.lower() in text else 'does not appear'} in source text."))
        if claim.get("claim_type") == "ontology_relationship":
            relationship = payload.get("relationship")
            quote = payload.get("literal_quote") or ""
            if evidence_type == "SOURCE_FACT":
                checks.append(self._check("source_fact_quote", bool(quote) and quote.lower() in text, "Source-fact literal quote appears verbatim in the source."))
                checks.append(self._check("source_fact_relationship", relationship in self._taxonomy_sets()["source_relationship"], "Source-fact relationship belongs to the source-fact vocabulary."))
            elif evidence_type == "MODEL_INFERENCE":
                checks.append(self._check("inference_separation", relationship in self._taxonomy_sets()["inference_relationship"], "Inference is explicitly represented using the inference-only relationship vocabulary."))
        passed = all(check["passed"] for check in checks)
        status = "inference_only" if evidence_type == "MODEL_INFERENCE" else ("passed" if passed else "failed")
        return {"status": status, "checked_at": utc_now(), "checks": checks, "trusted_graph_eligible": bool(passed and evidence_type == "SOURCE_FACT"), "reason": None if passed else "; ".join(check["name"] for check in checks if not check["passed"])}

    def validate_pending(self, claim_ids: list[str] | None = None, limit: int | None = None) -> dict[str, Any]:
        claims = self.claims.load()
        evidence_by_id = {row["id"]: row for row in self.evidence.load()}
        wanted = set(claim_ids or [])
        selected = [claim for claim in claims if claim.get("state") == "pending_validation" and (not wanted or claim.get("id") in wanted)]
        if limit:
            selected = selected[:limit]
        summary = {"validated": 0, "passed": 0, "failed": 0, "inference_only": 0, "missing_evidence": 0}
        for claim in selected:
            evidence = evidence_by_id.get(claim.get("evidence_id"))
            if not evidence:
                claim["validation"] = {"status": "failed", "checked_at": utc_now(), "checks": [self._check("evidence_exists", False, "Linked evidence record is missing.")], "trusted_graph_eligible": False, "reason": "evidence_exists"}
                claim["state"] = "review_required"
                summary["missing_evidence"] += 1
                continue
            validation = self.validate(claim, evidence, claims)
            claim["validation"] = validation
            claim["state"] = "review_required"
            claim["review_required"] = True
            summary["validated"] += 1
            summary[validation["status"]] = summary.get(validation["status"], 0) + 1
        self.claims.save(claims)
        return summary


def get_candidate_validator() -> CandidateValidator:
    return CandidateValidator()
