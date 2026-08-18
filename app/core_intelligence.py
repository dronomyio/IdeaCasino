"""Central read-only Idea Casino intelligence facade.

The Core API is the shared boundary for the web application, MCP server, and guided
skill. It exposes source-backed facts and accepted model inferences separately and
never triggers collection, enrichment, validation, review, mutation, or scoring side
effects.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .evidence_engine import get_evidence_engine
from .market import get_market_intelligence
from .metrics_engine import get_metrics_engine


class CoreIntelligence:
    def __init__(self):
        self.market = get_market_intelligence()
        self.evidence = get_evidence_engine()
        self.metrics = get_metrics_engine()

    @staticmethod
    def _date_cutoff(days: int) -> str:
        return (date.today() - timedelta(days=max(1, min(days, 3650)))).isoformat()

    @staticmethod
    def _record_source(claim: dict[str, Any], evidence_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
        source = evidence_by_id.get(claim.get("evidence_id"), {})
        return {
            "claim_id": claim.get("id"),
            "claim_type": claim.get("claim_type"),
            "provenance": claim.get("provenance", "source_fact"),
            "evidence_type": claim.get("evidence_type", "SOURCE_FACT"),
            "validation_status": (claim.get("validation") or {}).get("status"),
            "observed_at": claim.get("observed_at"),
            "source_url": source.get("canonical_url") or source.get("url"),
            "source_title": source.get("title"),
            "source_date": source.get("published_date"),
            "literal_quote": (claim.get("payload") or {}).get("literal_quote"),
        }

    def _claims(self, include_inferences: bool = False) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        evidence_by_id = {row.get("id"): row for row in self.evidence.evidence.load() if row.get("id")}
        trusted = []
        for claim in self.evidence.claims.load():
            source_fact = claim.get("state") in {"verified", "manual_confirmed"} and claim.get("evidence_type", "SOURCE_FACT") in {"SOURCE_FACT", "MODEL_CLASSIFICATION"}
            inference = include_inferences and claim.get("state") == "accepted_inference" and claim.get("evidence_type") == "MODEL_INFERENCE"
            if source_fact or inference:
                trusted.append(claim)
        return trusted, evidence_by_id

    def _metric_values(self, market_id: str | None = None) -> list[dict[str, Any]]:
        rows = self.metrics.list_snapshots(market_id=market_id)
        return [row for row in rows if row.get("status") == "calculated"]

    def catalog(self) -> dict[str, Any]:
        meta = self.market.raw().get("meta", {})
        markets = self.market.markets()
        claims, _ = self._claims(include_inferences=True)
        return {
            "service": "Idea Casino Core Intelligence API",
            "version": "1.0.0",
            "read_only": True,
            "data_status": meta.get("data_status", "unknown"),
            "markets": len(markets),
            "trusted_source_facts": sum(1 for claim in claims if claim.get("evidence_type") != "MODEL_INFERENCE"),
            "accepted_model_inferences": sum(1 for claim in claims if claim.get("evidence_type") == "MODEL_INFERENCE"),
            "boundaries": {
                "observed_facts": "Only verified or manually confirmed source facts are returned as observed facts.",
                "model_inferences": "Accepted model inferences are optional, separately labelled, and excluded from formula-backed metrics.",
                "profile_data": "Seeded market profiles are labelled demonstration data until replaced by verified evidence.",
            },
        }

    def search_funding(self, theme: str | None = None, days: int = 365, limit: int = 50) -> dict[str, Any]:
        cutoff = self._date_cutoff(days)
        claims, evidence_by_id = self._claims()
        rows = []
        needle = (theme or "").lower().strip()
        for claim in claims:
            if claim.get("claim_type") != "funding_round" or claim.get("observed_at", "") < cutoff:
                continue
            payload = claim.get("payload") or {}
            haystack = " ".join(str(payload.get(key, "")) for key in ("company", "theme", "subcategory", "application", "market_id")).lower()
            if needle and needle not in haystack:
                continue
            rows.append({**payload, "source": self._record_source(claim, evidence_by_id)})
        rows.sort(key=lambda row: (row.get("announced_date") or row.get("source", {}).get("source_date") or "", row.get("amount_usd") or 0), reverse=True)
        return {"query": {"theme": theme, "days": days}, "cutoff": cutoff, "records": rows[:max(1, min(limit, 200))], "provenance": "verified_source_facts", "note": "No demonstration funding profiles are substituted when verified funding evidence is unavailable."}

    def get_investor_graph(self, company: str, include_inferences: bool = False) -> dict[str, Any]:
        claims, evidence_by_id = self._claims(include_inferences=include_inferences)
        needle = company.lower().strip()
        records = []
        for claim in claims:
            payload = claim.get("payload") or {}
            if str(payload.get("company", "")).lower() != needle or claim.get("claim_type") not in {"investor_participation", "partner_participation", "funding_round"}:
                continue
            records.append({"claim_type": claim.get("claim_type"), "payload": payload, "source": self._record_source(claim, evidence_by_id)})
        return {"company": company, "relationships": records, "provenance": "verified_source_facts" if not include_inferences else "verified_source_facts_plus_accepted_inferences", "note": "An empty result means the reviewed evidence store has no admissible relationship, not that no investor relationship exists."}

    def get_partner_activity(self, partner: str, days: int = 365) -> dict[str, Any]:
        cutoff = self._date_cutoff(days)
        claims, evidence_by_id = self._claims()
        needle = partner.lower().strip()
        rows = []
        for claim in claims:
            payload = claim.get("payload") or {}
            if claim.get("claim_type") != "partner_participation" or str(payload.get("partner", "")).lower() != needle or claim.get("observed_at", "") < cutoff:
                continue
            rows.append({"company": payload.get("company"), "firm": payload.get("firm"), "role": payload.get("role"), "round_date": payload.get("round_date"), "stage": payload.get("stage"), "source": self._record_source(claim, evidence_by_id)})
        return {"partner": partner, "days": days, "activity": sorted(rows, key=lambda row: row.get("round_date") or "", reverse=True), "provenance": "verified_source_facts"}

    def compare_builder_vs_capital(self, theme: str, include_demo_profiles: bool = True) -> dict[str, Any]:
        needle = theme.lower().strip()
        markets = [market for market in self.market.markets() if needle in " ".join([market["theme"], market["name"], market["subcategory"]]).lower()]
        if not markets:
            return {"theme": theme, "markets": [], "note": "No tracked market profile matches this theme."}
        results = []
        for market in markets:
            snapshots = {snapshot.get("metric"): snapshot for snapshot in self._metric_values(market["id"])}
            capital = snapshots.get("capital_deployed") or {}
            builders = snapshots.get("active_builders") or {}
            has_live = bool(capital and builders)
            results.append({
                "market_id": market["id"], "market": market["name"], "capital_velocity": market["capital"]["velocity_90d"] if include_demo_profiles and not has_live else None,
                "builder_velocity": market["builders"]["velocity_90d"] if include_demo_profiles and not has_live else None,
                "capital_deployed_usd": capital.get("value"), "active_builders": builders.get("value"),
                "capital_per_builder": (snapshots.get("capital_per_builder") or {}).get("value"),
                "classification": market["classification"],
                "data_tier": "verified_metric_snapshot" if has_live else "demonstration_profile",
            })
        return {"theme": theme, "markets": results, "note": "Demonstration profile values are explicitly labelled and should not be interpreted as verified live measurements."}

    def find_underbuilt_markets(self, limit: int = 10, include_demo_profiles: bool = True) -> dict[str, Any]:
        markets = [market for market in self.market.markets(status="Underbuilt")]
        items = []
        for market in markets[:max(1, min(limit, 50))]:
            items.append({"market_id": market["id"], "market": market["name"], "classification": market["classification"], "idea_odds": market["scores"]["opportunity"], "capital_velocity": market["capital"]["velocity_90d"], "builder_velocity": market["builders"]["velocity_90d"], "data_tier": "demonstration_profile" if include_demo_profiles else "profile_excluded"})
        return {"markets": items if include_demo_profiles else [], "classification_rule": "Stored market profile classification; verified metric snapshots remain the production calculation source.", "data_status": self.market.raw().get("meta", {}).get("data_status")}

    def get_pain_graph(self, theme: str, limit: int = 10, include_demo_profiles: bool = True) -> dict[str, Any]:
        comparison = self.compare_builder_vs_capital(theme, include_demo_profiles=include_demo_profiles)
        markets = [self.market.market(item["market_id"]) for item in comparison.get("markets", [])]
        pains = []
        for market in markets:
            if not market:
                continue
            for pain in market.get("pain_points", []):
                pains.append({"market_id": market["id"], "market": market["name"], **pain, "data_tier": "demonstration_profile"})
        pains.sort(key=lambda point: point.get("opportunity", 0), reverse=True)
        return {"theme": theme, "pain_points": pains[:max(1, min(limit, 50))] if include_demo_profiles else [], "note": "Pain scores are only production-grade when produced by the reviewed evidence metric pipeline; profile values are demonstrations."}

    def check_idea(self, idea: str, geography: str | None = None, stage: str | None = None, include_demo_profiles: bool = True) -> dict[str, Any]:
        result = self.market.idea_odds(idea)
        market = result["matched_market"]
        live_metrics = self._metric_values(market["id"])
        observed, evidence_by_id = self._claims()
        market_claims = [claim for claim in observed if (claim.get("payload") or {}).get("market_id") == market["id"]]
        response = {
            "idea": idea, "geography": geography, "stage": stage,
            "theme": market["theme"], "subcategory": market["name"], "classification": result["verdict"],
            "capital_velocity": market["capital"]["velocity_90d"] if include_demo_profiles else None,
            "builder_velocity": market["builders"]["velocity_90d"] if include_demo_profiles else None,
            "demand_velocity": market["demand"]["score"] if include_demo_profiles else None,
            "competition": market["competition"]["label"] if include_demo_profiles else None,
            "pain_points": [item["name"] for item in result["picks_shovels"]],
            "investor_matches": market.get("smart_money", []) if include_demo_profiles else [],
            "metric_snapshots": live_metrics,
            "evidence": [self._record_source(claim, evidence_by_id) for claim in market_claims[:20]],
            "data_tier": "demonstration_profile" if include_demo_profiles else "verified_evidence_only",
            "disclaimer": result["disclaimer"],
            "limitations": ["Geography and stage are echoed as query context until reviewed evidence captures those fields.", "Investor matches from profiles are illustrative until backed by verified investor participation claims."],
        }
        return response

    def explain_idea_odds(self, idea: str, include_demo_profiles: bool = True) -> dict[str, Any]:
        result = self.market.idea_odds(idea)
        if not include_demo_profiles:
            result = {key: value for key, value in result.items() if key not in {"matched_market", "why", "picks_shovels", "active_investors", "adjacent_markets", "idea_odds", "verdict"}}
        return {"analysis": result, "evidence_boundary": "Profile-derived explanations are demonstrations. Live calculations require reviewed source claims and versioned metric snapshots."}


def get_core_intelligence() -> CoreIntelligence:
    return CoreIntelligence()
