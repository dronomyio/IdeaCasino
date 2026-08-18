"""Evidence-first Tavily ingestion for the Idea Casino data engine.

This module never treats a search hit as a production fact. It stores immutable
source evidence first, creates normalized claims in review_required state, and
only exposes verified/manual_confirmed claims to downstream calculations.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests

from .normalizer import normalize_document

PROJECT_DATA = Path(__file__).resolve().parents[1] / "data"
MONEY_RE = re.compile(r"\$(\d+(?:\.\d+)?)\s*(billion|million|bn|b|m)\b", re.I)
STAGE_RE = re.compile(r"\b(pre[- ]?seed|seed|series\s+[a-h]|growth|venture|strategic)\b", re.I)
ROUND_RE = re.compile(r"\b(raises?|raised|funding|financing|round|backs?|invests?|investment)\b", re.I)
OUTCOME_RE = re.compile(r"\b(acquired|acquisition|ipo|initial public offering|shutdown|shut down|ceased operations|bankruptcy)\b", re.I)
REVENUE_RE = re.compile(r"\b(arr|annual recurring revenue|revenue|run rate|contract value|bookings)\b", re.I)
PROCUREMENT_RE = re.compile(r"\b(rfp|request for proposal|procurement|tender|contract award|solicitation)\b", re.I)
JOB_RE = re.compile(r"\b(job|jobs|hiring|career|careers|engineer|manager|director)\b", re.I)
LEAD_FIRM_RE = re.compile(r"\b(?:led by|co-led by)\s+([A-Z][A-Za-z0-9& .\-]{2,70}?)(?=\s+(?:with|alongside|and|in)|[,.;])", re.I)
PARTNER_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2}),?\s+(?:a\s+|the\s+)?(?:general\s+|managing\s+)?partner\s+(?:at|from)\s+([A-Z][A-Za-z0-9& .\-]{2,70}?)(?=\s+(?:led|joined|invested|backed)|[,.;])", re.I)


MARKET_HINTS = {
    "mcp-security": ["mcp security", "model context protocol", "agent permission", "agent security", "prompt injection", "runtime sandbox"],
    "agent-observability": ["agent observability", "llm observability", "tracing", "evaluation", "evals", "telemetry"],
    "physical-ai-infrastructure": ["physical ai", "robotics", "humanoid", "robot", "simulation", "fleet telemetry", "sensor integration"],
    "ai-coding-agents": ["coding agent", "ai coding", "code generation", "developer tools", "code review"],
    "ai-sdr-agents": ["sdr", "sales agent", "outbound", "revenue automation", "prospecting"],
    "stablecoin-infrastructure": ["stablecoin", "payments", "settlement", "wallet", "treasury", "liquidity routing"],
    "voice-agent-compliance": ["voice agent", "voice ai", "audio provenance", "voice fraud", "voice compliance"],
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_url(url: str) -> str:
    try:
        parts = urlsplit(url.strip())
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", ""))
    except Exception:
        return url.strip()


def stable_id(*parts: str) -> str:
    joined = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:24]


def amount_usd(text: str) -> int | None:
    match = MONEY_RE.search(text or "")
    if not match:
        return None
    value, unit = float(match.group(1)), match.group(2).lower()
    return int(value * (1_000_000_000 if unit in {"billion", "bn", "b"} else 1_000_000))


def stage(text: str) -> str | None:
    match = STAGE_RE.search(text or "")
    return match.group(1).title().replace("Pre Seed", "Pre-Seed") if match else None


def company_from_title(title: str) -> str | None:
    if not title:
        return None
    patterns = [
        r"^\s*([^|:–—-]{2,80}?)\s+(?:raises?|raised|lands?|secures?|closes?|gets?|bags?)\b",
        r"\bfor\s+([A-Z][A-Za-z0-9 .&+\-]{1,60})(?:[,;:]|$)",
        r"^\s*([A-Z][A-Za-z0-9.&+\-]{1,55})\s*[:|–—-]",
    ]
    for pattern in patterns:
        match = re.search(pattern, title, re.I)
        if match:
            return match.group(1).strip(" '\"")
    return None


def market_from_text(text: str) -> str | None:
    lowered = (text or "").lower()
    ranked = [(sum(token in lowered for token in tokens), market_id) for market_id, tokens in MARKET_HINTS.items()]
    score, market_id = max(ranked, default=(0, None))
    return market_id if score else None


def published_date(result: dict[str, Any]) -> str | None:
    for field in ("published_date", "publishedDate", "date"):
        if result.get(field):
            return str(result[field])[:10]
    return None


class JsonList:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        value = json.loads(self.path.read_text())
        return value if isinstance(value, list) else value.get("records", [])

    def save(self, rows: list[dict[str, Any]]) -> None:
        self.path.write_text(json.dumps(rows, indent=2, ensure_ascii=False))

    def upsert(self, rows: list[dict[str, Any]], key: str = "id") -> tuple[int, int]:
        existing = self.load()
        indexed = {row.get(key): row for row in existing if row.get(key)}
        created = updated = 0
        for row in rows:
            row_key = row.get(key)
            if row_key in indexed:
                indexed[row_key].update(row)
                updated += 1
            else:
                existing.append(row)
                indexed[row_key] = row
                created += 1
        self.save(existing)
        return created, updated


class EvidenceEngine:
    def __init__(self):
        configured_data = Path(os.getenv("DATA_DIR", "/app/data"))
        self.data_dir = configured_data if configured_data.exists() else PROJECT_DATA
        self.criteria_path = Path(os.getenv("SEARCH_CRITERIA_FILE", str(self.data_dir / "search_criteria.json")))
        if not self.criteria_path.exists():
            self.criteria_path = PROJECT_DATA / "search_criteria.json"
        self.raw_path = Path(os.getenv("TAVILY_EVIDENCE_RAW_FILE", str(self.data_dir / "tavily_evidence_raw.json")))
        self.evidence = JsonList(Path(os.getenv("EVIDENCE_FILE", str(self.data_dir / "evidence.json"))))
        self.claims = JsonList(Path(os.getenv("CLAIMS_FILE", str(self.data_dir / "claims.json"))))
        self.runs = JsonList(Path(os.getenv("COLLECTION_RUNS_FILE", str(self.data_dir / "collection_runs.json"))))
        self.endpoint = os.getenv("TAVILY_SEARCH_URL", "https://api.tavily.com/search")
        self.api_key = os.getenv("TAVILY_API_KEY", "")

    def criteria_config(self) -> dict[str, Any]:
        return json.loads(self.criteria_path.read_text())

    def criteria(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        records = self.criteria_config().get("criteria", [])
        return [record for record in records if record.get("enabled", True)] if enabled_only else records

    def _request(self, query: str, criterion: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key or self.api_key == "tvly-YOUR_API_KEY":
            raise RuntimeError("TAVILY_API_KEY is not configured. Add a real key to .env before running a collection.")
        payload = {
            "api_key": self.api_key,
            "query": query,
            "topic": criterion.get("topic", "general"),
            "search_depth": criterion.get("search_depth", defaults.get("search_depth", "basic")),
            "max_results": int(criterion.get("max_results", defaults.get("max_results", 10))),
            "chunks_per_source": int(criterion.get("chunks_per_source", defaults.get("chunks_per_source", 3))),
            "include_answer": False,
            "include_raw_content": criterion.get("include_raw_content", defaults.get("include_raw_content", "markdown")),
            "include_usage": True,
            "time_range": criterion.get("time_range", defaults.get("time_range", "month")),
            "include_domains": criterion.get("include_domains", []),
        }
        if criterion.get("exclude_domains"):
            payload["exclude_domains"] = criterion["exclude_domains"]
        retries = int(defaults.get("max_retries", 3))
        for attempt in range(retries):
            try:
                response = requests.post(self.endpoint, json=payload, timeout=90)
                if response.status_code in {429, 500, 502, 503, 504} and attempt < retries - 1:
                    time.sleep(int(defaults.get("backoff_seconds", 2)) * (2 ** attempt))
                    continue
                response.raise_for_status()
                return response.json()
            except requests.RequestException:
                if attempt == retries - 1:
                    raise
                time.sleep(int(defaults.get("backoff_seconds", 2)) * (2 ** attempt))
        raise RuntimeError("Tavily request retry loop ended unexpectedly")

    def _evidence_record(self, result: dict[str, Any], criterion: dict[str, Any], run_id: str) -> dict[str, Any]:
        document = normalize_document(result, source_type="news", criterion_id=criterion["id"], run_id=run_id)
        document.update({
            "id": stable_id(document["canonical_url"], document["content_hash"]),
            "signal_type": criterion.get("signal_type"),
            "tavily_score": float(result.get("score") or 0),
            "raw_reference": {"run_id": run_id, "result_id": result.get("id"), "raw_content_available": bool(result.get("raw_content"))},
        })
        return document

    def _claim(self, evidence: dict[str, Any], claim_type: str, payload: dict[str, Any], confidence: int, reason: str) -> dict[str, Any]:
        claim_id = stable_id(evidence["id"], claim_type, json.dumps(payload, sort_keys=True))
        return {
            "id": claim_id,
            "evidence_id": evidence["id"],
            "claim_type": claim_type,
            "payload": payload,
            "confidence": confidence,
            "extraction_method": "deterministic_heuristic_v1",
            "extraction_reason": reason,
            "observed_at": evidence.get("published_date") or evidence["retrieved_at"][:10],
            "state": "review_required",
            "review_required": True,
            "reviewed_at": None,
            "reviewed_by": None,
            "review_note": None,
        }

    def _normalize(self, evidence: dict[str, Any], criterion: dict[str, Any]) -> list[dict[str, Any]]:
        text = f"{evidence['title']}\n{evidence['content_excerpt']}"
        market = market_from_text(text)
        company = company_from_title(evidence["title"])
        signal_type = criterion.get("signal_type")
        claims: list[dict[str, Any]] = []
        common = {"market_id": market, "company": company, "source_url": evidence["url"]}

        if "funding_round" in criterion.get("claim_types", []) and ROUND_RE.search(text):
            amount = amount_usd(text)
            round_stage = stage(text)
            confidence = 20 + (30 if company else 0) + (25 if amount else 0) + (15 if round_stage else 0) + (10 if market else 0)
            claims.append(self._claim(evidence, "funding_round", {
                **common, "amount_usd": amount, "stage": round_stage, "announced_date": evidence.get("published_date"),
            }, confidence, "Funding verb plus deterministic amount/stage/company extraction."))
            lead_match = LEAD_FIRM_RE.search(text)
            if lead_match:
                firm = lead_match.group(1).strip()
                claims.append(self._claim(evidence, "investor_participation", {
                    **common, "firm": firm, "role": "lead", "round_date": evidence.get("published_date"), "stage": round_stage,
                }, 48 + (20 if company else 0) + (15 if amount else 0), "Lead-investor phrase extraction; requires firm and round verification."))
            for partner_match in PARTNER_RE.finditer(text):
                partner, firm = partner_match.group(1).strip(), partner_match.group(2).strip()
                claims.append(self._claim(evidence, "partner_participation", {
                    **common, "partner": partner, "firm": firm, "role": "participant", "round_date": evidence.get("published_date"), "stage": round_stage,
                }, 46 + (20 if company else 0), "Partner-at-firm phrase extraction; requires human verification."))

        if signal_type == "builder":
            claim_type = "builder_project" if "github" in criterion["id"] else "builder_company"
            claims.append(self._claim(evidence, claim_type, {
                **common, "project_url": evidence["url"], "observed_date": evidence.get("published_date") or evidence["retrieved_at"][:10],
            }, 45 + (20 if market else 0), "Builder criterion source and market-keyword match."))
            if "cohort" in criterion["id"]:
                claims.append(self._claim(evidence, "accelerator_cohort", {
                    **common, "accelerator": "Unknown", "cohort": None,
                }, 35 + (20 if company else 0), "Accelerator cohort criterion; requires human verification."))

        if signal_type == "demand":
            claim_type = "procurement_event" if PROCUREMENT_RE.search(text) else ("job_posting" if JOB_RE.search(text) else "customer_adoption")
            claims.append(self._claim(evidence, claim_type, {
                **common, "buyer": None, "amount_usd": amount_usd(text), "observed_date": evidence.get("published_date") or evidence["retrieved_at"][:10],
            }, 42 + (20 if market else 0), "Demand criterion and deterministic event keyword match."))

        if signal_type == "revenue" and REVENUE_RE.search(text):
            claims.append(self._claim(evidence, "revenue_event", {
                **common, "amount_usd": amount_usd(text), "metric": "reported_revenue_or_traction", "observed_date": evidence.get("published_date"),
            }, 38 + (25 if company else 0) + (15 if amount_usd(text) else 0), "Revenue/traction keyword and amount extraction."))

        if signal_type == "outcome" and OUTCOME_RE.search(text):
            outcome = OUTCOME_RE.search(text).group(1).lower()
            claims.append(self._claim(evidence, "outcome_event", {
                **common, "outcome_type": outcome, "observed_date": evidence.get("published_date"),
            }, 45 + (25 if company else 0), "Outcome keyword match; requires company and outcome verification."))

        if signal_type == "pain":
            pain = next((token for token in ["runtime sandboxing", "agent permission testing", "security evaluation", "simulation", "safety validation", "sensor integration", "fleet telemetry", "compliance", "observability"] if token in text.lower()), None)
            claims.append(self._claim(evidence, "technology_dependency", {
                **common, "technology": None, "pain_point": pain, "dependency_weight": None,
            }, 35 + (25 if market else 0) + (15 if pain else 0), "Dependency criterion with market/pain keyword match."))

        if signal_type == "competition":
            claims.append(self._claim(evidence, "competitor", {
                **common, "competitor": company, "observed_date": evidence.get("published_date") or evidence["retrieved_at"][:10],
            }, 30 + (25 if company else 0) + (20 if market else 0), "Vendor/competitor criterion and named-company extraction."))

        if market and company:
            claims.append(self._claim(evidence, "company_classification", {
                "company": company, "market_id": market, "source_url": evidence["url"],
            }, 45 + (20 if evidence["tavily_score"] >= 0.65 else 0), "Company-title extraction and market taxonomy keyword match."))
        return claims

    def collect(self, criterion_ids: list[str] | None = None, max_queries: int | None = None) -> dict[str, Any]:
        config = self.criteria_config()
        defaults = config.get("collection_defaults", {})
        selected = self.criteria(enabled_only=True)
        if criterion_ids:
            selected = [criterion for criterion in selected if criterion["id"] in set(criterion_ids)]
        tasks = [(criterion, query) for criterion in selected for query in criterion.get("queries", [])]
        if max_queries:
            tasks = tasks[:max_queries]
        run_id = str(uuid.uuid4())
        run = {"id": run_id, "started_at": utc_now(), "status": "running", "queries": [], "usage_credits": 0}
        raw_searches: list[dict[str, Any]] = []
        evidence_rows: list[dict[str, Any]] = []
        claim_rows: list[dict[str, Any]] = []
        for criterion, query in tasks:
            query_record = {"criterion_id": criterion["id"], "query": query, "started_at": utc_now()}
            try:
                response = self._request(query, criterion, defaults)
                query_record.update({"state": "succeeded", "request_id": response.get("request_id"), "results": len(response.get("results", [])), "usage": response.get("usage", {})})
                run["usage_credits"] += int((response.get("usage") or {}).get("credits") or 0)
                raw_searches.append({"run_id": run_id, "criterion_id": criterion["id"], "query": query, "response": response})
                for result in response.get("results", []) or []:
                    if float(result.get("score") or 0) < float(defaults.get("min_relevance_score", 0.45)):
                        continue
                    record = self._evidence_record(result, criterion, run_id)
                    evidence_rows.append(record)
                    # Classification is intentionally deferred to enrich.py / LLMEnricher.
                    # Normalization preserves a source document only; it does not infer ontology claims.
            except Exception as exc:
                query_record.update({"state": "failed", "error": str(exc)})
            query_record["completed_at"] = utc_now()
            run["queries"].append(query_record)
        prior_raw = []
        if self.raw_path.exists():
            prior_raw = json.loads(self.raw_path.read_text())
        self.raw_path.write_text(json.dumps(prior_raw + raw_searches, indent=2, ensure_ascii=False))
        evidence_created, evidence_updated = self.evidence.upsert(evidence_rows)
        claims_created, claims_updated = self.claims.upsert(claim_rows)
        run.update({
            "completed_at": utc_now(),
            "status": "completed" if all(item["state"] == "succeeded" for item in run["queries"]) else "completed_with_errors",
            "evidence_created": evidence_created,
            "evidence_updated": evidence_updated,
            "claims_created": claims_created,
            "claims_updated": claims_updated,
        })
        self.runs.upsert([run])
        return run

    def list_evidence(self, state: str | None = None, criterion_id: str | None = None) -> list[dict[str, Any]]:
        rows = self.evidence.load()
        if state:
            rows = [row for row in rows if row.get("state") == state]
        if criterion_id:
            rows = [row for row in rows if row.get("criterion_id") == criterion_id]
        return sorted(rows, key=lambda row: row.get("retrieved_at", ""), reverse=True)

    def list_claims(self, state: str | None = None, claim_type: str | None = None) -> list[dict[str, Any]]:
        rows = self.claims.load()
        if state:
            rows = [row for row in rows if row.get("state") == state]
        if claim_type:
            rows = [row for row in rows if row.get("claim_type") == claim_type]
        return sorted(rows, key=lambda row: row.get("observed_at", ""), reverse=True)

    def review_claim(self, claim_id: str, decision: str, reviewer: str, note: str | None = None, payload_patch: dict[str, Any] | None = None) -> dict[str, Any]:
        if decision not in {"verified", "rejected", "manual_confirmed", "accepted_inference"}:
            raise ValueError("Decision must be verified, rejected, manual_confirmed, or accepted_inference.")
        rows = self.claims.load()
        for claim in rows:
            if claim.get("id") == claim_id:
                evidence_type = claim.get("evidence_type", "SOURCE_FACT")
                validation_status = (claim.get("validation") or {}).get("status")
                if decision == "verified" and evidence_type != "SOURCE_FACT":
                    raise ValueError("Only source-fact candidates can be verified. Use accepted_inference for model inferences.")
                if decision == "verified" and validation_status != "passed":
                    raise ValueError("Run validation first; only candidates with validation.status=passed can be verified.")
                if decision == "accepted_inference" and evidence_type != "MODEL_INFERENCE":
                    raise ValueError("accepted_inference is reserved for MODEL_INFERENCE candidates.")
                if payload_patch:
                    claim["payload"].update(payload_patch)
                claim.update({
                    "state": decision,
                    "review_required": False,
                    "reviewed_at": utc_now(),
                    "reviewed_by": reviewer or "operator",
                    "review_note": note,
                })
                self.claims.save(rows)
                return claim
        raise KeyError("Claim not found")

    def summary(self) -> dict[str, Any]:
        claims = self.claims.load()
        states: dict[str, int] = {}
        types: dict[str, int] = {}
        for claim in claims:
            states[claim.get("state", "unknown")] = states.get(claim.get("state", "unknown"), 0) + 1
            types[claim.get("claim_type", "unknown")] = types.get(claim.get("claim_type", "unknown"), 0) + 1
        return {
            "criteria": len(self.criteria()),
            "enabled_criteria": len(self.criteria(enabled_only=True)),
            "collection_runs": len(self.runs.load()),
            "evidence": len(self.evidence.load()),
            "claims": len(claims),
            "claims_by_state": states,
            "claims_by_type": types,
            "production_input_policy": "Only verified or manual_confirmed claims contribute to calculations.",
        }


def get_evidence_engine() -> EvidenceEngine:
    return EvidenceEngine()
