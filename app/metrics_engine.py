"""Versioned, evidence-gated calculations for the Idea Casino market engine."""
from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .evidence_engine import JsonList, stable_id

PROJECT_DATA = Path(__file__).resolve().parents[1] / "data"
PRODUCTION_STATES = {"verified", "manual_confirmed"}


def parse_day(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None


def clamp(value: float, low: float = 0.01, high: float = 1.0) -> float:
    return max(low, min(high, value))


def minmax(values: dict[str, float | None]) -> dict[str, float | None]:
    present = [value for value in values.values() if value is not None]
    if not present:
        return {key: None for key in values}
    low, high = min(present), max(present)
    if high == low:
        return {key: (1.0 if value is not None and value > 0 else (0.01 if value is not None else None)) for key, value in values.items()}
    return {key: (clamp((value - low) / (high - low)) if value is not None else None) for key, value in values.items()}


class MetricsEngine:
    def __init__(self):
        configured_data = Path(os.getenv("DATA_DIR", "/app/data"))
        self.data_dir = configured_data if configured_data.exists() else PROJECT_DATA
        self.definitions_path = Path(os.getenv("CALCULATION_DEFINITIONS_FILE", str(self.data_dir / "calculation_definitions.json")))
        if not self.definitions_path.exists():
            self.definitions_path = PROJECT_DATA / "calculation_definitions.json"
        self.taxonomy_path = Path(os.getenv("MARKET_INTELLIGENCE_FILE", str(self.data_dir / "market_intelligence.json")))
        if not self.taxonomy_path.exists():
            self.taxonomy_path = PROJECT_DATA / "market_intelligence.json"
        self.claims = JsonList(Path(os.getenv("CLAIMS_FILE", str(self.data_dir / "claims.json"))))
        self.snapshots = JsonList(Path(os.getenv("METRIC_SNAPSHOTS_FILE", str(self.data_dir / "metric_snapshots.json"))))

    def definitions(self) -> dict[str, Any]:
        return json.loads(self.definitions_path.read_text())

    def taxonomy(self) -> dict[str, Any]:
        return json.loads(self.taxonomy_path.read_text()) if self.taxonomy_path.exists() else {"markets": []}

    def verified_claims(self) -> list[dict[str, Any]]:
        return [claim for claim in self.claims.load() if claim.get("state") in PRODUCTION_STATES]

    def market_metadata(self) -> dict[str, dict[str, Any]]:
        return {market["id"]: market for market in self.taxonomy().get("markets", [])}

    def markets(self, claims: list[dict[str, Any]]) -> list[str]:
        observed = {str((claim.get("payload") or {}).get("market_id")) for claim in claims if (claim.get("payload") or {}).get("market_id")}
        return sorted(observed)

    @staticmethod
    def in_window(claim: dict[str, Any], start: date, end: date) -> bool:
        observed = parse_day(claim.get("observed_at"))
        return bool(observed and start <= observed <= end)

    @staticmethod
    def claim_value(claim: dict[str, Any], field: str) -> float | None:
        value = (claim.get("payload") or {}).get(field)
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _market_claims(claims: list[dict[str, Any]], market_id: str) -> list[dict[str, Any]]:
        return [claim for claim in claims if (claim.get("payload") or {}).get("market_id") == market_id]

    def _raw(self, claims: list[dict[str, Any]], market_id: str, current_start: date, current_end: date, previous_start: date, previous_end: date, theme_market_count: int) -> dict[str, Any]:
        rows = self._market_claims(claims, market_id)
        current = [claim for claim in rows if self.in_window(claim, current_start, current_end)]
        previous = [claim for claim in rows if self.in_window(claim, previous_start, previous_end)]

        def of_type(items: list[dict[str, Any]], *types: str) -> list[dict[str, Any]]:
            return [item for item in items if item.get("claim_type") in types]

        funding_current = of_type(current, "funding_round")
        funding_previous = of_type(previous, "funding_round")
        capital_current = sum(self.claim_value(item, "amount_usd") or 0 for item in funding_current)
        capital_previous = sum(self.claim_value(item, "amount_usd") or 0 for item in funding_previous)
        capital_velocity = ((capital_current - capital_previous) / max(capital_previous, 1_000_000)) if funding_current or funding_previous else None

        builder_types = ("builder_project", "builder_company", "accelerator_cohort", "product_launch")
        builders_current_rows = of_type(current, *builder_types)
        builders_previous_rows = of_type(previous, *builder_types)
        builder_keys_current = {str((item.get("payload") or {}).get("project_url") or (item.get("payload") or {}).get("company") or item["id"]) for item in builders_current_rows}
        builder_keys_previous = {str((item.get("payload") or {}).get("project_url") or (item.get("payload") or {}).get("company") or item["id"]) for item in builders_previous_rows}
        builders_current, builders_previous = len(builder_keys_current), len(builder_keys_previous)
        builder_velocity = ((builders_current - builders_previous) / max(builders_previous, 1)) if builders_current or builders_previous else None

        demand_weights = {"job_posting": 1, "customer_adoption": 3, "procurement_event": 5, "contract_event": 5}
        demand_current = sum(demand_weights.get(item.get("claim_type"), 0) for item in current)
        demand_previous = sum(demand_weights.get(item.get("claim_type"), 0) for item in previous)
        demand_growth = ((demand_current - demand_previous) / max(demand_previous, 1)) if demand_current or demand_previous else None

        revenue_claims = of_type(current, "revenue_event", "contract_event")
        revenue_amount = sum(self.claim_value(item, "amount_usd") or 0 for item in revenue_claims)
        revenue_signal = math.log1p(revenue_amount) if revenue_claims else None

        competitors = of_type(current, "competitor", "vendor", "funding_round", "product_launch")
        competitor_keys = {str((item.get("payload") or {}).get("company") or (item.get("payload") or {}).get("competitor") or item["id"]) for item in competitors}
        competitive_count = len(competitor_keys) if competitors else None
        builder_density = (builders_current / max(theme_market_count, 1)) if builders_current else None
        capital_per_builder = (capital_current / builders_current) if builders_current else None

        dependencies = of_type(current, "technology_dependency", "pain_point")
        vendor_claims = of_type(current, "vendor", "competitor")
        dependency_weighted_capital = 0.0
        dependency_weights: list[float] = []
        pain_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        company_funding: dict[str, float] = defaultdict(float)
        for funding in funding_current:
            company_funding[str((funding.get("payload") or {}).get("company") or "")] += self.claim_value(funding, "amount_usd") or 0
        for dependency in dependencies:
            payload = dependency.get("payload") or {}
            weight = self.claim_value(dependency, "dependency_weight")
            if weight is not None:
                dependency_weights.append(weight)
                dependency_weighted_capital += company_funding.get(str(payload.get("company") or ""), 0) * weight
            if payload.get("pain_point"):
                pain_groups[str(payload["pain_point"])].append(dependency)

        adoption_signal = sum(1 for item in current if item.get("claim_type") in {"builder_project", "builder_company", "customer_adoption", "product_launch"})
        switching_cost_values = [self.claim_value(item, "switching_cost") for item in dependencies]
        switching_cost_values = [value for value in switching_cost_values if value is not None]
        monetization_signal = revenue_amount + sum(self.claim_value(item, "amount_usd") or 0 for item in of_type(current, "procurement_event", "contract_event"))

        pain_raw: dict[str, dict[str, Any]] = {}
        for pain_name, pain_rows in pain_groups.items():
            urgency_values = [self.claim_value(item, "urgency") for item in pain_rows]
            urgency_values = [value for value in urgency_values if value is not None]
            pain_density = 0.0
            pain_input_ids = []
            for dependency in pain_rows:
                payload = dependency.get("payload") or {}
                weight = self.claim_value(dependency, "dependency_weight")
                company = str(payload.get("company") or "")
                if weight is not None and company:
                    pain_density += company_funding.get(company, 0) * weight
                pain_input_ids.append(dependency["id"])
            pain_vendors = {str((item.get("payload") or {}).get("vendor") or (item.get("payload") or {}).get("competitor") or item["id"]) for item in vendor_claims}
            budget_amount = sum(self.claim_value(item, "amount_usd") or 0 for item in of_type(current, "procurement_event", "contract_event", "revenue_event"))
            pain_raw[pain_name] = {
                "pain_density": pain_density if pain_rows else None,
                "customer_growth": demand_growth,
                "urgency": (sum(urgency_values) / len(urgency_values)) if urgency_values else None,
                "budget_availability": math.log1p(budget_amount) if budget_amount else None,
                "existing_vendors": len(pain_vendors) if vendor_claims else None,
                "input_ids": pain_input_ids + [item["id"] for item in vendor_claims] + [item["id"] for item in funding_current],
            }

        downstream: dict[str, float] = defaultdict(float)
        for dependency in dependencies:
            payload = dependency.get("payload") or {}
            coefficient = self.claim_value(dependency, "spend_allocation_coefficient")
            company = str(payload.get("company") or "")
            category = str(payload.get("technology") or payload.get("pain_point") or "unspecified")
            if coefficient is not None and company:
                downstream[category] += company_funding.get(company, 0) * coefficient

        return {
            "capital_current": capital_current if funding_current else None,
            "capital_previous": capital_previous if funding_previous else None,
            "capital_velocity": capital_velocity,
            "round_count": len(funding_current),
            "active_builders": builders_current if builders_current_rows else None,
            "builder_velocity": builder_velocity,
            "capital_per_builder": capital_per_builder,
            "demand_signal": demand_current if demand_current else None,
            "demand_growth": demand_growth,
            "revenue_signal": revenue_signal,
            "revenue_amount": revenue_amount if revenue_claims else None,
            "builder_density": builder_density,
            "competitive_count": competitive_count,
            "dependency_signal": dependency_weighted_capital if dependency_weights else None,
            "adoption_signal": adoption_signal if adoption_signal else None,
            "switching_cost_signal": (sum(switching_cost_values) / len(switching_cost_values)) if switching_cost_values else None,
            "monetization_signal": math.log1p(monetization_signal) if monetization_signal else None,
            "pain_raw": pain_raw,
            "downstream": dict(downstream),
            "input_ids": [claim["id"] for claim in current],
        }

    def calculate(self, window_days: int | None = None, as_of: str | None = None) -> list[dict[str, Any]]:
        definitions = self.definitions()
        days = int(window_days or definitions.get("default_window_days", 90))
        end = parse_day(as_of) or datetime.now(timezone.utc).date()
        current_start = end - timedelta(days=days - 1)
        previous_end = current_start - timedelta(days=1)
        previous_start = previous_end - timedelta(days=days - 1)
        claims = self.verified_claims()
        markets = self.markets(claims)
        metadata = self.market_metadata()
        theme_count: dict[str, int] = defaultdict(int)
        for market_id in markets:
            theme_count[str((metadata.get(market_id) or {}).get("theme") or "unclassified")] += 1
        raw: dict[str, dict[str, Any]] = {}
        for market_id in markets:
            theme = str((metadata.get(market_id) or {}).get("theme") or "unclassified")
            raw[market_id] = self._raw(claims, market_id, current_start, end, previous_start, previous_end, theme_count[theme])

        normalized = {
            "capital_momentum": minmax({market_id: values["capital_velocity"] for market_id, values in raw.items()}),
            "buyer_demand": minmax({market_id: values["demand_signal"] for market_id, values in raw.items()}),
            "revenue_evidence": minmax({market_id: values["revenue_signal"] for market_id, values in raw.items()}),
            "builder_density": minmax({market_id: values["builder_density"] for market_id, values in raw.items()}),
            "competitive_intensity": minmax({market_id: values["competitive_count"] for market_id, values in raw.items()}),
            "dependency": minmax({market_id: values["dependency_signal"] for market_id, values in raw.items()}),
            "adoption": minmax({market_id: values["adoption_signal"] for market_id, values in raw.items()}),
            "switching_cost": minmax({market_id: values["switching_cost_signal"] for market_id, values in raw.items()}),
            "monetization": minmax({market_id: values["monetization_signal"] for market_id, values in raw.items()}),
        }

        snapshots: list[dict[str, Any]] = []
        metric_defs = definitions.get("metrics", {})
        formula_version = definitions.get("version", "1.0")
        for market_id, values in raw.items():
            inputs = values["input_ids"]
            base = {
                "market_id": market_id,
                "formula_version": formula_version,
                "window_start": current_start.isoformat(),
                "window_end": end.isoformat(),
                "calculated_at": datetime.now(timezone.utc).isoformat(),
                "input_ids": inputs,
                "input_count": len(inputs),
            }

            def add(metric: str, value: float | int | None, unit: str, status: str, detail: dict[str, Any]) -> None:
                snapshots.append({
                    **base,
                    "id": stable_id("snapshot", formula_version, market_id, metric, current_start.isoformat(), end.isoformat()),
                    "metric": metric,
                    "value": value,
                    "unit": unit,
                    "status": status,
                    "detail": {"formula": metric_defs.get(metric, {}).get("formula"), **detail},
                })

            add("capital_deployed", values["capital_current"], "USD", "computed" if values["capital_current"] is not None else "insufficient_evidence", {})
            add("round_count", values["round_count"], "count", "computed" if values["capital_current"] is not None else "insufficient_evidence", {})
            add("capital_velocity", values["capital_velocity"], "percent_change", "computed" if values["capital_velocity"] is not None else "insufficient_evidence", {"previous_window_capital": values["capital_previous"], "denominator_floor_usd": 1000000})
            add("active_builders", values["active_builders"], "count", "computed" if values["active_builders"] is not None else "insufficient_evidence", {})
            add("builder_velocity", values["builder_velocity"], "percent_change", "computed" if values["builder_velocity"] is not None else "insufficient_evidence", {})
            add("capital_per_builder", values["capital_per_builder"], "USD_per_builder", "computed" if values["capital_per_builder"] is not None else "insufficient_evidence", {})
            add("buyer_demand", normalized["buyer_demand"][market_id], "normalized_factor", "computed" if normalized["buyer_demand"][market_id] is not None else "insufficient_evidence", {"raw_weighted_event_count": values["demand_signal"], "demand_growth": values["demand_growth"]})
            add("revenue_evidence", normalized["revenue_evidence"][market_id], "normalized_factor", "computed" if normalized["revenue_evidence"][market_id] is not None else "insufficient_evidence", {"raw_revenue_amount": values["revenue_amount"]})
            add("builder_density", normalized["builder_density"][market_id], "normalized_factor", "computed" if normalized["builder_density"][market_id] is not None else "insufficient_evidence", {"raw_density": values["builder_density"]})
            add("competitive_intensity", normalized["competitive_intensity"][market_id], "normalized_factor", "computed" if normalized["competitive_intensity"][market_id] is not None else "insufficient_evidence", {"raw_competitor_count": values["competitive_count"]})

            opportunity_factors = [normalized["capital_momentum"][market_id], normalized["buyer_demand"][market_id], normalized["revenue_evidence"][market_id], normalized["builder_density"][market_id], normalized["competitive_intensity"][market_id]]
            if all(factor is not None for factor in opportunity_factors):
                numerator = opportunity_factors[0] * opportunity_factors[1] * opportunity_factors[2]
                denominator = max(opportunity_factors[3] * opportunity_factors[4], 0.01)
                opportunity = min(100.0, 100.0 * numerator / denominator)
                add("opportunity_score", opportunity, "score_0_to_100", "computed", {"factors": {"capital_momentum": opportunity_factors[0], "buyer_demand": opportunity_factors[1], "revenue_evidence": opportunity_factors[2], "builder_density": opportunity_factors[3], "competitive_intensity": opportunity_factors[4]}, "raw_quotient": numerator / denominator})
            else:
                add("opportunity_score", None, "score_0_to_100", "insufficient_evidence", {"missing_factors": [name for name, factor in zip(["capital_momentum", "buyer_demand", "revenue_evidence", "builder_density", "competitive_intensity"], opportunity_factors) if factor is None]})

            pss_factors = [normalized["dependency"][market_id], normalized["adoption"][market_id], normalized["switching_cost"][market_id], normalized["monetization"][market_id]]
            if all(factor is not None for factor in pss_factors):
                pss = 100.0 * math.prod(pss_factors)
                add("picks_and_shovels_score", pss, "score_0_to_100", "computed", {"factors": {"dependency": pss_factors[0], "adoption": pss_factors[1], "switching_cost": pss_factors[2], "monetization": pss_factors[3]}})
            else:
                add("picks_and_shovels_score", None, "score_0_to_100", "insufficient_evidence", {"missing_factors": [name for name, factor in zip(["dependency", "adoption", "switching_cost", "monetization"], pss_factors) if factor is None]})

            if normalized["builder_density"][market_id] is not None and normalized["competitive_intensity"][market_id] is not None and normalized["buyer_demand"][market_id] is not None:
                house_edge = 100.0 * (0.45 * normalized["builder_density"][market_id] + 0.35 * normalized["competitive_intensity"][market_id] + 0.20 * (1 - normalized["buyer_demand"][market_id]))
                add("house_edge", house_edge, "score_0_to_100", "computed", {})
            else:
                add("house_edge", None, "score_0_to_100", "insufficient_evidence", {})

            for category, value in values["downstream"].items():
                add(f"downstream_dollar::{category}", value, "USD_estimated_spend_exposure", "computed", {"category": category})

            for pain_name, pain in values["pain_raw"].items():
                pain_key = pain_name.lower().replace(" ", "_")
                add(f"pain_density::{pain_key}", pain["pain_density"], "USD_weighted_exposure", "computed" if pain["pain_density"] is not None else "insufficient_evidence", {"pain_point": pain_name, "input_ids": pain["input_ids"]})
                pain_factors = [pain["pain_density"], pain["customer_growth"], pain["urgency"], pain["budget_availability"], pain["existing_vendors"], values["builder_density"]]
                if all(factor is not None for factor in pain_factors):
                    numerator = pain_factors[0] * max(pain_factors[1], 0.01) * pain_factors[2] * pain_factors[3]
                    denominator = max(pain_factors[4] * pain_factors[5], 0.01)
                    add(f"pain_opportunity::{pain_key}", min(100.0, 100.0 * numerator / denominator), "score_0_to_100", "computed", {"pain_point": pain_name, "raw_quotient": numerator / denominator, "input_ids": pain["input_ids"]})
                else:
                    add(f"pain_opportunity::{pain_key}", None, "score_0_to_100", "insufficient_evidence", {"pain_point": pain_name, "missing_components": [name for name, factor in zip(["pain_density", "customer_growth", "urgency", "budget_availability", "existing_vendors", "builder_density"], pain_factors) if factor is None], "input_ids": pain["input_ids"]})

        self.snapshots.upsert(snapshots)
        return snapshots

    def list_snapshots(self, market_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        rows = self.snapshots.load()
        if market_id:
            rows = [row for row in rows if row.get("market_id") == market_id]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        return sorted(rows, key=lambda row: (row.get("market_id", ""), row.get("metric", "")))

    def drilldown(self, market_id: str) -> dict[str, Any]:
        snapshots = self.list_snapshots(market_id=market_id)
        claim_ids = sorted({claim_id for snapshot in snapshots for claim_id in snapshot.get("input_ids", [])})
        evidence = [claim for claim in self.verified_claims() if claim["id"] in set(claim_ids)]
        return {
            "market_id": market_id,
            "snapshots": snapshots,
            "supporting_claims": evidence,
            "calculation_definitions": self.definitions(),
            "input_policy": self.definitions().get("input_policy"),
        }


def get_metrics_engine() -> MetricsEngine:
    return MetricsEngine()
