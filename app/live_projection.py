"""Reviewed-evidence market projection for the Idea Casino landing dashboard.

This module is intentionally a one-way boundary: it reads only trusted source facts
and deterministic metric snapshots. It never falls back to seeded demonstration
profiles for values that are displayed as live market data.
"""
from __future__ import annotations

import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from .evidence_engine import get_evidence_engine
from .market import get_market_intelligence
from .metrics_engine import get_metrics_engine

TRUSTED_STATES = {"verified", "manual_confirmed"}
SIGNAL_BY_CLAIM = {
    "funding_round": "capital",
    "investor_participation": "capital",
    "partner_participation": "capital",
    "sec_filing": "capital",
    "builder_project": "builder",
    "builder_company": "builder",
    "accelerator_cohort": "builder",
    "product_launch": "builder",
    "technology_adoption": "builder",
    "job_posting": "demand",
    "customer_adoption": "demand",
    "procurement_event": "demand",
    "contract_event": "demand",
    "revenue_event": "revenue",
    "competitor": "competition",
    "vendor": "competition",
    "technology_dependency": "pain",
    "pain_point": "pain",
}


class LiveMarketProjection:
    """Build a dashboard-safe view using evidence that has cleared review."""

    def __init__(self) -> None:
        self.market = get_market_intelligence()
        self.evidence = get_evidence_engine()
        self.metrics = get_metrics_engine()
        self.minimum_claims = max(1, int(os.getenv("LIVE_PROJECTION_MIN_VERIFIED_CLAIMS", "3")))
        self.minimum_signal_types = max(1, int(os.getenv("LIVE_PROJECTION_MIN_SIGNAL_TYPES", "2")))
        self.required_metrics = tuple(
            item.strip()
            for item in os.getenv(
                "LIVE_PROJECTION_REQUIRED_METRICS",
                "capital_deployed,round_count,active_builders,buyer_demand",
            ).split(",")
            if item.strip()
        )

    def _metadata(self) -> dict[str, dict[str, Any]]:
        return {row["id"]: row for row in self.market.raw().get("markets", []) if row.get("id")}

    def _trusted_claims(self) -> list[dict[str, Any]]:
        return [
            row for row in self.evidence.claims.load()
            if row.get("state") in TRUSTED_STATES
            and row.get("evidence_type", "SOURCE_FACT") in {"SOURCE_FACT", "MODEL_CLASSIFICATION"}
        ]

    @staticmethod
    def _latest_snapshots(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for row in rows:
            if row.get("status") != "computed" or row.get("value") is None:
                continue
            metric = str(row.get("metric") or "")
            current = latest.get(metric)
            if not current or str(row.get("calculated_at") or "") > str(current.get("calculated_at") or ""):
                latest[metric] = row
        return latest

    @staticmethod
    def _format_usd(value: Any) -> str | None:
        if not isinstance(value, (int, float)):
            return None
        absolute = abs(float(value))
        if absolute >= 1_000_000_000:
            return f"${value / 1_000_000_000:.2f}B"
        if absolute >= 1_000_000:
            return f"${value / 1_000_000:.1f}M"
        if absolute >= 1_000:
            return f"${value / 1_000:.0f}K"
        return f"${value:.0f}"

    @staticmethod
    def _format_percent(value: Any) -> str | None:
        if not isinstance(value, (int, float)):
            return None
        return f"{value * 100:+.0f}%"

    def _market_row(self, market_id: str, claims: list[dict[str, Any]], metadata: dict[str, Any]) -> dict[str, Any]:
        snapshots = self._latest_snapshots(self.metrics.list_snapshots(market_id=market_id))
        signal_counts = Counter(SIGNAL_BY_CLAIM.get(row.get("claim_type"), "other") for row in claims)
        missing_metrics = [metric for metric in self.required_metrics if metric not in snapshots]
        populated_signal_types = [key for key in ("capital", "builder", "demand") if signal_counts.get(key, 0) > 0]
        coverage = {
            "verified_claims": len(claims),
            "signal_counts": dict(signal_counts),
            "signal_types": populated_signal_types,
            "minimum_verified_claims": self.minimum_claims,
            "minimum_signal_types": self.minimum_signal_types,
            "required_metrics": list(self.required_metrics),
            "missing_metrics": missing_metrics,
        }
        qualifies = (
            len(claims) >= self.minimum_claims
            and len(populated_signal_types) >= self.minimum_signal_types
            and not missing_metrics
        )
        if qualifies:
            quality = "verified_live"
            status_label = "Verified live evidence"
        elif claims:
            quality = "provisional_reviewed_evidence"
            status_label = "Reviewed evidence, incomplete coverage"
        else:
            quality = "insufficient_evidence"
            status_label = "Insufficient reviewed evidence"

        # A computed snapshot alone is not a live dashboard conclusion. Surface
        # numeric values only after the reviewed-evidence coverage threshold is met.
        display_snapshots = snapshots if qualifies else {}
        capital = display_snapshots.get("capital_deployed", {}).get("value")
        rounds = display_snapshots.get("round_count", {}).get("value")
        builders = display_snapshots.get("active_builders", {}).get("value")
        demand = display_snapshots.get("buyer_demand", {}).get("value")
        velocity = display_snapshots.get("capital_velocity", {}).get("value")
        opportunity = display_snapshots.get("opportunity_score", {}).get("value")
        return {
            "market_id": market_id,
            "name": metadata.get("name") or market_id.replace("-", " ").title(),
            "theme": metadata.get("theme") or "Unclassified",
            "subcategory": metadata.get("subcategory") or "Unclassified",
            "quality": quality,
            "quality_label": status_label,
            "coverage": coverage,
            "metrics": {
                "capital_deployed_usd": capital,
                "capital_deployed_label": self._format_usd(capital),
                "round_count": rounds,
                "active_builders": builders,
                "buyer_demand_factor": demand,
                "capital_velocity": velocity,
                "capital_velocity_label": self._format_percent(velocity),
                "opportunity_score": opportunity,
                "opportunity_score_label": f"{opportunity:.0f}" if isinstance(opportunity, (int, float)) else None,
            },
            "metric_snapshots": display_snapshots,
            "claim_ids": [row.get("id") for row in claims],
            "data_tier": "reviewed_evidence_only",
        }

    def dashboard(self) -> dict[str, Any]:
        metadata = self._metadata()
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for claim in self._trusted_claims():
            market_id = (claim.get("payload") or {}).get("market_id")
            if market_id:
                grouped[str(market_id)].append(claim)

        market_ids = sorted(set(metadata) | set(grouped))
        markets = [self._market_row(market_id, grouped.get(market_id, []), metadata.get(market_id, {})) for market_id in market_ids]
        quality_counts = Counter(row["quality"] for row in markets)
        live_markets = [row for row in markets if row["quality"] == "verified_live"]
        theme_capital: dict[str, float] = defaultdict(float)
        for row in live_markets:
            value = row["metrics"].get("capital_deployed_usd")
            if isinstance(value, (int, float)):
                theme_capital[row["theme"]] += float(value)
        observed_at = datetime.now(timezone.utc).isoformat()
        return {
            "projection": "reviewed_evidence_live_market_projection",
            "generated_at": observed_at,
            "display_policy": {
                "live_values": "Only reviewed source facts and deterministic computed metric snapshots are displayed as live values.",
                "provisional_values": "Reviewed evidence with incomplete coverage is displayed without numeric market conclusions.",
                "demo_profiles": "Demonstration profile values are deliberately excluded from this projection.",
            },
            "summary": {
                "tracked_markets": len(markets),
                "verified_live_markets": quality_counts.get("verified_live", 0),
                "provisional_markets": quality_counts.get("provisional_reviewed_evidence", 0),
                "insufficient_evidence_markets": quality_counts.get("insufficient_evidence", 0),
                "verified_capital_total_usd": sum(theme_capital.values()),
                "verified_capital_total_label": self._format_usd(sum(theme_capital.values())) if theme_capital else None,
            },
            "theme_allocation": [
                {"theme": theme, "capital_deployed_usd": value, "capital_deployed_label": self._format_usd(value)}
                for theme, value in sorted(theme_capital.items(), key=lambda item: item[1], reverse=True)
            ],
            "markets": markets,
        }


def get_live_market_projection() -> LiveMarketProjection:
    return LiveMarketProjection()
