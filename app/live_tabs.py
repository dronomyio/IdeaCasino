"""Strict reviewed-evidence projections for Idea Casino’s non-landing tabs.

The existing ``market.py`` service remains available only as a legacy demonstration
fixture. Product-facing tabs use this module, which projects configured taxonomy,
reviewed source facts, and deterministic metric snapshots without profile fallbacks.
"""
from __future__ import annotations

from typing import Any

from .live_projection import LiveMarketProjection, get_live_market_projection


class LiveTabProjection:
    """Expose safe table, money-flow, pain, and graph payloads for the UI."""

    def __init__(self, projection: LiveMarketProjection | None = None) -> None:
        self.projection = projection or get_live_market_projection()

    @staticmethod
    def _format_usd(value: Any) -> str | None:
        return LiveMarketProjection._format_usd(value)

    @staticmethod
    def _policy() -> dict[str, str]:
        return {
            "live_values": "Only reviewed source facts and deterministic computed metric snapshots are displayed as live values.",
            "provisional_values": "Reviewed evidence with incomplete coverage is displayed without numeric market conclusions.",
            "demo_profiles": "Demonstration profile values are deliberately excluded from every product-facing tab.",
            "taxonomy": "Configured theme and subcategory labels are taxonomy context, not market measurements.",
        }

    def markets(self) -> dict[str, Any]:
        dashboard = self.projection.dashboard()
        return {
            "projection": "reviewed_evidence_live_market_table",
            "generated_at": dashboard["generated_at"],
            "display_policy": self._policy(),
            "summary": dashboard["summary"],
            "markets": dashboard["markets"],
        }

    def _market(self, market_id: str) -> dict[str, Any] | None:
        return next((market for market in self.projection.dashboard()["markets"] if market["market_id"] == market_id), None)

    def money_path(self, market_id: str) -> dict[str, Any] | None:
        market = self._market(market_id)
        if not market:
            return None
        allocations: list[dict[str, Any]] = []
        if market["quality"] == "verified_live":
            for metric, snapshot in market["metric_snapshots"].items():
                if not metric.startswith("downstream_dollar::"):
                    continue
                value = snapshot.get("value")
                if not isinstance(value, (int, float)):
                    continue
                category = str((snapshot.get("detail") or {}).get("category") or metric.split("::", 1)[1]).replace("_", " ")
                allocations.append({
                    "category": category,
                    "value_usd": value,
                    "value_label": self._format_usd(value),
                    "supporting_claim_ids": snapshot.get("input_ids", []),
                })
        total = sum(float(row["value_usd"]) for row in allocations)
        for row in allocations:
            row["share_percent"] = round(float(row["value_usd"]) * 100 / total, 1) if total else None
        allocations.sort(key=lambda row: row["value_usd"], reverse=True)
        return {
            "projection": "reviewed_evidence_live_money_path",
            "display_policy": self._policy(),
            "market": market,
            "downstream_dollars": allocations,
            "total_downstream_dollar_usd": total if allocations else None,
            "total_downstream_dollar_label": self._format_usd(total) if allocations else None,
            "status": {
                "numeric_values_available": bool(allocations),
                "reason": "Computed from reviewed technology-dependency claims and reviewed capital exposures." if allocations else "No downstream-dollar value is shown until reviewed evidence qualifies the market and a deterministic downstream snapshot exists.",
            },
        }

    def pain_graph(self, market_id: str) -> dict[str, Any] | None:
        market = self._market(market_id)
        if not market:
            return None
        pains_by_key: dict[str, dict[str, Any]] = {}
        if market["quality"] == "verified_live":
            for metric, snapshot in market["metric_snapshots"].items():
                if not metric.startswith(("pain_density::", "pain_opportunity::")):
                    continue
                detail = snapshot.get("detail") or {}
                key = metric.split("::", 1)[1]
                pain = pains_by_key.setdefault(key, {
                    "name": detail.get("pain_point") or key.replace("_", " ").title(),
                    "pain_density_usd": None,
                    "pain_density_label": None,
                    "opportunity_score": None,
                    "opportunity_score_label": None,
                    "supporting_claim_ids": [],
                })
                pain["supporting_claim_ids"] = sorted(set(pain["supporting_claim_ids"]) | set(snapshot.get("input_ids", [])))
                if metric.startswith("pain_density::"):
                    pain["pain_density_usd"] = snapshot.get("value")
                    pain["pain_density_label"] = self._format_usd(snapshot.get("value"))
                else:
                    value = snapshot.get("value")
                    pain["opportunity_score"] = value
                    pain["opportunity_score_label"] = f"{value:.0f}" if isinstance(value, (int, float)) else None
        pains = sorted(
            pains_by_key.values(),
            key=lambda row: float(row["opportunity_score"]) if isinstance(row["opportunity_score"], (int, float)) else -1,
            reverse=True,
        )
        return {
            "projection": "reviewed_evidence_live_pain_graph",
            "display_policy": self._policy(),
            "market": market,
            "pain_points": pains,
            "status": {
                "numeric_values_available": bool(pains),
                "reason": "Computed pain metrics are supported by reviewed claims and deterministic formula snapshots." if pains else "No pain score or exposure is shown until reviewed dependency, capital, demand, vendor, and budget inputs permit a deterministic calculation.",
            },
        }

    def market_graph(self) -> dict[str, Any]:
        dashboard = self.projection.dashboard()
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        theme_ids: dict[str, str] = {}
        for market in dashboard["markets"]:
            theme = market["theme"]
            theme_id = theme_ids.setdefault(theme, f"theme:{theme}")
            if not any(node["id"] == theme_id for node in nodes):
                nodes.append({"id": theme_id, "label": theme, "type": "Theme", "value": 18})
            market_node_id = f"market:{market['market_id']}"
            nodes.append({
                "id": market_node_id,
                "label": f"{market['name']}\n{market['quality_label']}",
                "type": "Market",
                "value": 14 + min(market["coverage"]["verified_claims"], 20),
            })
            edges.append({"from": theme_id, "to": market_node_id, "label": "taxonomy context"})
            if market["quality"] != "verified_live":
                continue
            signal_specs = (
                ("capital_deployed_usd", "capital_deployed_label", "CapitalSignal", "reviewed capital"),
                ("active_builders", "active_builders", "BuilderSignal", "reviewed builders"),
                ("buyer_demand_factor", "buyer_demand_factor", "DemandSignal", "reviewed demand"),
            )
            for value_key, label_key, node_type, caption in signal_specs:
                value = market["metrics"].get(value_key)
                if value is None:
                    continue
                label = market["metrics"].get(label_key)
                signal_id = f"signal:{market['market_id']}:{value_key}"
                nodes.append({"id": signal_id, "label": f"{label}\n{caption}", "type": node_type, "value": 18})
                edges.append({"from": market_node_id, "to": signal_id, "label": "computed from reviewed claims"})
        return {
            "projection": "reviewed_evidence_live_market_graph",
            "display_policy": self._policy(),
            "nodes": nodes,
            "edges": edges,
            "note": "The graph contains configured taxonomy context plus reviewed claims and deterministic metrics only. Seeded demonstration profile nodes and edges are excluded.",
        }


def get_live_tab_projection() -> LiveTabProjection:
    return LiveTabProjection()
