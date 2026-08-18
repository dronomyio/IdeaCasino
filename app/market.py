"""Filesystem-first Idea Casino market-intelligence calculations."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

PROJECT_DATA = Path(__file__).resolve().parents[1] / "data"


def _data_path() -> Path:
    configured = Path(os.getenv("MARKET_INTELLIGENCE_FILE", "/app/data/market_intelligence.json"))
    return configured if configured.exists() else PROJECT_DATA / "market_intelligence.json"


def _clamp(value: float) -> int:
    return max(0, min(100, round(value)))


def _compact_usd(value: int | float) -> str:
    number = float(value or 0)
    if number >= 1_000_000_000:
        return f"${number / 1_000_000_000:.1f}B"
    if number >= 1_000_000:
        return f"${number / 1_000_000:.0f}M"
    return f"${number:,.0f}"


class MarketIntelligence:
    """Read and calculate the UI-ready market model from a JSON profile file."""

    def __init__(self, source_file: Path | None = None):
        self.source_file = source_file or _data_path()

    def raw(self) -> dict[str, Any]:
        if not self.source_file.exists():
            return {"meta": {}, "markets": []}
        return json.loads(self.source_file.read_text())

    @staticmethod
    def _scores(market: dict[str, Any]) -> dict[str, int]:
        capital = market["capital"]
        builders = market["builders"]
        demand = market["demand"]
        competition = market["competition"]
        infrastructure = market["infrastructure"]
        capital_score = _clamp(46 + float(capital["velocity_90d"]) * 0.55 + min(capital["rounds"], 25) * 0.9)
        demand_score = _clamp(float(demand["score"]) * 0.72 + float(demand["revenue_evidence"]) * 0.28)
        timing_score = _clamp(capital_score * 0.45 + demand_score * 0.35 + (100 - builders["density"]) * 0.20)
        crowding_score = _clamp(builders["density"] * 0.70 + competition["score"] * 0.30)
        opportunity_score = _clamp(
            capital_score * 0.23
            + demand_score * 0.31
            + timing_score * 0.16
            + (100 - crowding_score) * 0.18
            + infrastructure["score"] * 0.12
        )
        house_edge = _clamp(crowding_score * 0.65 + (100 - demand_score) * 0.20 + (100 - capital_score) * 0.15)
        return {
            "capital": capital_score,
            "demand": demand_score,
            "timing": timing_score,
            "builder_crowding": crowding_score,
            "competition": int(competition["score"]),
            "picks_shovels": int(infrastructure["score"]),
            "opportunity": opportunity_score,
            "house_edge": house_edge,
        }

    @staticmethod
    def _classification(scores: dict[str, int], market: dict[str, Any]) -> str:
        if scores["builder_crowding"] >= 76 and scores["demand"] < 75:
            return "Overheated"
        if scores["picks_shovels"] >= 84 and scores["opportunity"] >= 70:
            return "Picks & Shovels"
        if scores["opportunity"] >= 76 and scores["builder_crowding"] <= 58:
            return "Underbuilt"
        if market["capital"]["velocity_90d"] >= 35 and market["builders"]["velocity_90d"] <= 35:
            return "Emerging"
        if scores["opportunity"] < 48:
            return "Cold"
        return "Watchlist"

    def enrich(self, market: dict[str, Any]) -> dict[str, Any]:
        profile = json.loads(json.dumps(market))
        profile["scores"] = self._scores(profile)
        profile["classification"] = self._classification(profile["scores"], profile)
        profile["formatted"] = {
            "capital": _compact_usd(profile["capital"]["amount_usd"]),
            "pain_exposed": _compact_usd(sum(point["capital_exposed_usd"] for point in profile.get("pain_points", []))),
        }
        return profile

    def markets(self, status: str | None = None, term: str | None = None) -> list[dict[str, Any]]:
        profiles = [self.enrich(record) for record in self.raw().get("markets", [])]
        if status:
            profiles = [profile for profile in profiles if profile["classification"].lower() == status.lower().strip()]
        if term:
            needle = term.lower().strip()
            profiles = [
                profile for profile in profiles
                if needle in " ".join([profile["name"], profile["theme"], profile["subcategory"], *profile.get("keywords", [])]).lower()
            ]
        return sorted(profiles, key=lambda profile: profile["scores"]["opportunity"], reverse=True)

    def market(self, market_id: str) -> dict[str, Any] | None:
        return next((profile for profile in self.markets() if profile["id"] == market_id), None)

    def dashboard(self) -> dict[str, Any]:
        markets = self.markets()
        classification_counts: dict[str, int] = {}
        for market in markets:
            category = market["classification"]
            classification_counts[category] = classification_counts.get(category, 0) + 1
        total_capital = sum(market["capital"]["amount_usd"] for market in markets)
        return {
            "meta": self.raw().get("meta", {}),
            "summary": {
                "tracked_markets": len(markets),
                "capital_observed_usd": total_capital,
                "capital_observed": _compact_usd(total_capital),
                "rounds": sum(market["capital"]["rounds"] for market in markets),
                "active_builders": sum(market["builders"]["active"] for market in markets),
                "classifications": classification_counts,
            },
            "underbuilt": [market for market in markets if market["classification"] == "Underbuilt"][:3],
            "overheated": [market for market in markets if market["classification"] == "Overheated"][:3],
            "picks": [market for market in markets if market["classification"] == "Picks & Shovels"][:3],
            "hot_tables": sorted(markets, key=lambda market: market["capital"]["velocity_90d"], reverse=True)[:4],
            "markets": markets,
        }

    def idea_odds(self, query: str) -> dict[str, Any]:
        cleaned = re.sub(r"\s+", " ", query or "").strip()
        if not cleaned:
            raise ValueError("Enter an idea, technology, or market to check the odds.")
        query_tokens = set(re.findall(r"[a-z0-9]{3,}", cleaned.lower()))
        candidates: list[tuple[int, dict[str, Any]]] = []
        for market in self.markets():
            searchable = " ".join([market["name"], market["theme"], market["subcategory"], *market.get("keywords", []), *[point["name"] for point in market.get("pain_points", [])]]).lower()
            overlap = len(query_tokens & set(re.findall(r"[a-z0-9]{3,}", searchable)))
            exact_bonus = 16 if market["name"].lower() in cleaned.lower() else 0
            candidates.append((overlap * 12 + exact_bonus, market))
        candidates.sort(key=lambda item: (item[0], item[1]["scores"]["opportunity"]), reverse=True)
        relevance, match = candidates[0]
        adjacent = [item[1] for item in candidates[1:4] if item[0] > 0]
        scores = match["scores"]
        explanations = {
            "Underbuilt": "Demand and capital are rising faster than visible builder and competitor density.",
            "Overheated": "Builder and competitor density are high relative to measured demand evidence.",
            "Picks & Shovels": "This layer can earn from the broader ecosystem rather than a single application winner.",
            "Emerging": "Capital and attention are increasing while the category is still forming.",
            "Watchlist": "Signals are mixed; monitor the evidence before committing to a category bet.",
            "Cold": "The current signal mix does not show a supportive opportunity profile.",
        }
        return {
            "query": cleaned,
            "matched_market": match,
            "match_confidence": min(100, 45 + relevance * 2),
            "idea_odds": scores["opportunity"],
            "verdict": match["classification"],
            "explanation": explanations[match["classification"]],
            "why": [
                {"label": "Capital momentum", "value": scores["capital"], "detail": f"{match['formatted']['capital']} observed; {match['capital']['velocity_90d']:+d}% 90-day velocity."},
                {"label": "Buyer demand", "value": scores["demand"], "detail": match["demand"]["evidence"]},
                {"label": "Timing", "value": scores["timing"], "detail": f"{match['capital']['rounds']} relevant rounds and {match['capital']['active_vcs']} active firms in the profile."},
                {"label": "Competition", "value": scores["competition"], "detail": f"{match['competition']['funded_competitors']} funded competitors; {match['competition']['label'].lower()} intensity."},
                {"label": "Builder crowding", "value": scores["builder_crowding"], "detail": f"{match['builders']['active']:,} active builders; {match['builders']['density_label'].lower()} density."},
            ],
            "picks_shovels": sorted(match.get("pain_points", []), key=lambda point: point["opportunity"], reverse=True)[:3],
            "active_investors": match.get("smart_money", [])[:3],
            "adjacent_markets": adjacent,
            "disclaimer": self.raw().get("meta", {}).get("disclaimer", "Research information only; not investment or business advice."),
        }

    def money_path(self, market_id: str) -> dict[str, Any] | None:
        market = self.market(market_id)
        if not market:
            return None
        nodes = [
            {"id": "capital", "label": market["formatted"]["capital"], "type": "Capital", "value": market["capital"]["amount_usd"]},
            {"id": "market", "label": market["name"], "type": "Market", "value": market["capital"]["amount_usd"]},
        ]
        edges = [{"from": "capital", "to": "market", "label": "funds"}]
        for index, allocation in enumerate(market.get("downstream_dollars", [])):
            node_id = f"allocation-{index}"
            nodes.append({"id": node_id, "label": f"{allocation['name']}\n{allocation['share']}%", "type": "Spend", "value": allocation["share"]})
            edges.append({"from": "market", "to": node_id, "label": f"{allocation['share']}% estimated"})
        for index, pain in enumerate(market.get("pain_points", [])[:5]):
            node_id = f"pain-{index}"
            nodes.append({"id": node_id, "label": pain["name"], "type": "PainPoint", "value": pain["opportunity"]})
            edges.append({"from": "market", "to": node_id, "label": "creates demand"})
        return {"market": market, "nodes": nodes, "edges": edges}

    def pain_graph(self, market_id: str) -> dict[str, Any] | None:
        market = self.market(market_id)
        if not market:
            return None
        pains = sorted(market.get("pain_points", []), key=lambda point: point["opportunity"], reverse=True)
        nodes = [{"id": "market", "label": market["name"], "type": "Market", "value": market["scores"]["opportunity"]}]
        edges: list[dict[str, str]] = []
        for index, pain in enumerate(pains):
            pain_id = f"pain-{index}"
            vendor_id = f"vendor-{index}"
            nodes.extend([
                {"id": pain_id, "label": pain["name"], "type": "PainPoint", "value": pain["opportunity"]},
                {"id": vendor_id, "label": f"{pain['vendors']} vendor alternatives", "type": "Vendor", "value": max(12, 100 - pain["vendors"] * 3)},
            ])
            edges.extend([
                {"from": "market", "to": pain_id, "label": "needs"},
                {"from": pain_id, "to": vendor_id, "label": "served by"},
            ])
        return {"market": market, "pain_points": pains, "nodes": nodes, "edges": edges}

    def market_graph(self) -> dict[str, Any]:
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, str]] = []
        theme_ids: set[str] = set()
        for market in self.markets():
            theme_id = f"theme-{market['theme'].lower().replace(' ', '-').replace('/', '-')}"
            if theme_id not in theme_ids:
                nodes.append({"id": theme_id, "label": market["theme"], "type": "Theme", "value": 70})
                theme_ids.add(theme_id)
            market_id = f"market-{market['id']}"
            nodes.append({"id": market_id, "label": market["name"], "type": "Market", "value": market["scores"]["opportunity"], "classification": market["classification"]})
            edges.append({"from": theme_id, "to": market_id, "label": "contains"})
            for index, pain in enumerate(sorted(market.get("pain_points", []), key=lambda point: point["opportunity"], reverse=True)[:2]):
                pain_id = f"{market_id}-pain-{index}"
                nodes.append({"id": pain_id, "label": pain["name"], "type": "PainPoint", "value": pain["opportunity"]})
                edges.append({"from": market_id, "to": pain_id, "label": "creates demand"})
        return {"nodes": nodes, "edges": edges}

    def methodology(self) -> dict[str, Any]:
        return {
            "formula": "Opportunity = 23% capital momentum + 31% buyer demand + 16% timing + 18% uncrowded-market signal + 12% picks-and-shovels signal",
            "definitions": [
                {"term": "Capital momentum", "definition": "A transparent index derived from 90-day capital velocity and observed relevant rounds."},
                {"term": "Buyer demand", "definition": "A weighted combination of demand evidence and revenue/procurement evidence."},
                {"term": "Builder crowding", "definition": "A combined index of builder density and competitor intensity; higher is more crowded."},
                {"term": "Picks & Shovels", "definition": "A proxy for dependency, adoption, switching cost, and monetization potential of infrastructure layers."},
                {"term": "Pain opportunity", "definition": "A proxy based on capital exposed, affected companies, urgency, budget availability, vendor density, and builder density."},
            ],
            "disclaimer": self.raw().get("meta", {}).get("disclaimer", "Research information only; not investment or business advice."),
        }


def get_market_intelligence() -> MarketIntelligence:
    return MarketIntelligence()
