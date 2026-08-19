"""Structured GitHub and SEC EDGAR collectors for the unified Idea Casino pipeline.

Both collectors preserve the original source record, produce canonical evidence, and
emit review-required claims. They never promote API fields directly into production
metrics; only the existing evidence review workflow can do that.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from .normalizer import normalize_document
from .evidence_engine import JsonList, stable_id, utc_now

PROJECT_DATA = Path(__file__).resolve().parents[1] / "data"


def parse_bool(value: str | bool | None, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def parse_targets(value: str | None) -> list[dict[str, Any]]:
    if not value:
        return []
    decoded = json.loads(value)
    if not isinstance(decoded, list):
        raise ValueError("SEC_EDGAR_CIK_TARGETS must be a JSON array of CIK target objects.")
    return [item for item in decoded if isinstance(item, dict)]


def normalize_cik(value: str | int) -> str:
    raw = "".join(ch for ch in str(value) if ch.isdigit())
    if not raw or len(raw) > 10:
        raise ValueError("SEC CIK must contain between one and ten digits.")
    return raw.zfill(10)


def normalized_entity_name(value: str | None) -> str:
    """Normalize an SEC entity name for conservative CIK matching."""
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def date_or_none(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            return datetime.fromisoformat(str(value)[:10]).replace(tzinfo=timezone.utc)
        except ValueError:
            return None


class DirectCollectors:
    def __init__(self):
        configured_data = Path(os.getenv("DATA_DIR", "/app/data"))
        self.data_dir = configured_data if configured_data.exists() else PROJECT_DATA
        self.criteria_path = Path(os.getenv("DIRECT_API_CRITERIA_FILE", str(self.data_dir / "direct_api_criteria.json")))
        if not self.criteria_path.exists():
            self.criteria_path = PROJECT_DATA / "direct_api_criteria.json"
        self.evidence = JsonList(Path(os.getenv("EVIDENCE_FILE", str(self.data_dir / "evidence.json"))))
        self.claims = JsonList(Path(os.getenv("CLAIMS_FILE", str(self.data_dir / "claims.json"))))
        self.runs = JsonList(Path(os.getenv("COLLECTION_RUNS_FILE", str(self.data_dir / "collection_runs.json"))))
        self.raw = JsonList(Path(os.getenv("DIRECT_API_RAW_FILE", str(self.data_dir / "direct_api_raw.json"))))
        self.timeout = int(os.getenv("DIRECT_API_TIMEOUT_SECONDS", "45"))

    def config(self) -> dict[str, Any]:
        return json.loads(self.criteria_path.read_text())

    def _claim(self, evidence: dict[str, Any], claim_type: str, payload: dict[str, Any], confidence: int, method: str, reason: str) -> dict[str, Any]:
        claim_id = stable_id(evidence["id"], claim_type, json.dumps(payload, sort_keys=True, default=str))
        return {
            "id": claim_id,
            "evidence_id": evidence["id"],
            "claim_type": claim_type,
            "payload": payload,
            "confidence": confidence,
            "extraction_method": method,
            "extraction_reason": reason,
            "observed_at": evidence.get("published_date") or evidence["retrieved_at"][:10],
            "evidence_type": "SOURCE_FACT",
            "provenance": "source_fact",
            "state": "pending_validation",
            "review_required": True,
            "validation": {"status": "pending", "checks": []},
            "reviewed_at": None,
            "reviewed_by": None,
            "review_note": None,
        }

    @staticmethod
    def _evidence(source: str, source_url: str, title: str, content: str, observed_at: str | None, run_id: str, signal_type: str, external_id: str) -> dict[str, Any]:
        document = normalize_document({"url": source_url, "title": title, "content": content, "published_date": observed_at, "external_id": external_id}, source_type=source, criterion_id=source, run_id=run_id)
        document.update({
            "id": stable_id(source, external_id, document["content_hash"]),
            "signal_type": signal_type,
            "tavily_score": None,
            "raw_reference": {"run_id": run_id, "source": source, "external_id": external_id, "raw_content_available": True},
        })
        return document

    def _finish_run(self, run: dict[str, Any], raw_rows: list[dict[str, Any]], evidence_rows: list[dict[str, Any]], claim_rows: list[dict[str, Any]]) -> dict[str, Any]:
        self.raw.upsert(raw_rows)
        created_evidence, updated_evidence = self.evidence.upsert(evidence_rows)
        created_claims, updated_claims = self.claims.upsert(claim_rows)
        run.update({
            "completed_at": utc_now(),
            "status": "completed" if not run.get("errors") else "completed_with_errors",
            "evidence_created": created_evidence,
            "evidence_updated": updated_evidence,
            "claims_created": created_claims,
            "claims_updated": updated_claims,
        })
        self.runs.upsert([run])
        return run

    def _github_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": os.getenv("GITHUB_API_VERSION", "2022-11-28"),
            "User-Agent": os.getenv("GITHUB_USER_AGENT", "idea-casino-collector"),
        }
        token = os.getenv("GITHUB_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        elif parse_bool(os.getenv("GITHUB_REQUIRE_TOKEN"), True):
            raise RuntimeError("GITHUB_TOKEN is required because GITHUB_REQUIRE_TOKEN=true.")
        return headers

    def _github_get(self, path: str, params: dict[str, Any] | None = None) -> tuple[dict[str, Any] | list[Any], dict[str, str]]:
        response = requests.get(f"{os.getenv('GITHUB_API_BASE_URL', 'https://api.github.com').rstrip('/')}{path}", headers=self._github_headers(), params=params, timeout=self.timeout)
        if response.status_code == 403 and response.headers.get("X-RateLimit-Remaining") == "0":
            reset = response.headers.get("X-RateLimit-Reset", "unknown")
            raise RuntimeError(f"GitHub API rate limit exhausted; reset epoch is {reset}.")
        response.raise_for_status()
        return response.json(), dict(response.headers)

    def _github_activity(self, owner: str, repo: str) -> dict[str, Any]:
        metrics: dict[str, Any] = {"recent_commit_count": None, "contributor_count": None, "stats_state": "not_requested"}
        if not parse_bool(os.getenv("GITHUB_INCLUDE_ACTIVITY_STATS"), True):
            return metrics
        delay = float(os.getenv("GITHUB_REQUEST_DELAY_SECONDS", "0.15"))
        time.sleep(delay)
        try:
            activity, headers = self._github_get(f"/repos/{owner}/{repo}/stats/commit_activity")
            if isinstance(activity, list):
                metrics["recent_commit_count"] = sum(int(row.get("total", 0)) for row in activity[-4:])
                metrics["stats_state"] = "available"
            else:
                metrics["stats_state"] = "pending"
            time.sleep(delay)
            contributors, _ = self._github_get(f"/repos/{owner}/{repo}/contributors", {"per_page": 100, "anon": "true"})
            if isinstance(contributors, list):
                metrics["contributor_count"] = len(contributors)
        except requests.RequestException as exc:
            metrics["stats_state"] = f"unavailable: {exc.__class__.__name__}"
        return metrics

    def collect_github(self, max_results: int | None = None, market_ids: list[str] | None = None) -> dict[str, Any]:
        section = self.config().get("github", {})
        run_id = str(uuid.uuid4())
        run = {"id": run_id, "collector": "github_rest", "started_at": utc_now(), "status": "running", "queries": [], "errors": []}
        raw_rows: list[dict[str, Any]] = []
        evidence_rows: list[dict[str, Any]] = []
        claim_rows: list[dict[str, Any]] = []
        if not section.get("enabled", False):
            run["errors"].append("GitHub collector is disabled in direct_api_criteria.json.")
            return self._finish_run(run, raw_rows, evidence_rows, claim_rows)
        per_query = min(int(max_results or os.getenv("GITHUB_MAX_RESULTS_PER_QUERY", "20")), 100)
        searches = section.get("searches", [])
        if market_ids:
            allowed_markets = set(market_ids)
            searches = [search for search in searches if search.get("market_id") in allowed_markets]
        for search in searches:
            query_log = {"criterion_id": search["id"], "query": search["query"], "started_at": utc_now()}
            try:
                payload, headers = self._github_get("/search/repositories", {"q": search["query"], "sort": search.get("sort", "updated"), "order": search.get("order", "desc"), "per_page": per_query})
                items = payload.get("items", []) if isinstance(payload, dict) else []
                query_log.update({"state": "succeeded", "results": len(items), "incomplete_results": bool(payload.get("incomplete_results")) if isinstance(payload, dict) else False, "rate_limit_remaining": headers.get("X-RateLimit-Remaining")})
                for repository in items:
                    owner = (repository.get("owner") or {}).get("login") or "unknown"
                    name = repository.get("name") or "unknown"
                    activity = self._github_activity(owner, name)
                    source_url = repository.get("html_url") or f"https://github.com/{owner}/{name}"
                    observed_at = repository.get("pushed_at") or repository.get("updated_at") or repository.get("created_at")
                    record = {"repository": repository, "activity": activity, "search": search, "run_id": run_id}
                    external_id = str(repository.get("id") or repository.get("full_name") or source_url)
                    evidence = self._evidence("github_rest", source_url, f"GitHub repository: {repository.get('full_name')}", json.dumps(record, default=str, sort_keys=True), observed_at, run_id, "builder", external_id)
                    evidence_rows.append(evidence)
                    raw_rows.append({"id": stable_id("github_raw", external_id, str(repository.get("updated_at"))), "source": "github_rest", "run_id": run_id, "external_id": external_id, "retrieved_at": evidence["retrieved_at"], "payload": record})
                    project_payload = {
                        "market_id": search["market_id"], "company": owner, "project_url": source_url, "repository": repository.get("full_name"), "repository_id": repository.get("id"), "description": repository.get("description"), "stars": repository.get("stargazers_count"), "forks": repository.get("forks_count"), "open_issues": repository.get("open_issues_count"), "subscribers": repository.get("subscribers_count"), "watchers": repository.get("watchers_count"), "language": repository.get("language"), "topics": repository.get("topics", []), "created_at": repository.get("created_at"), "pushed_at": repository.get("pushed_at"), "updated_at": repository.get("updated_at"), **activity,
                    }
                    claim_rows.append(self._claim(evidence, "builder_project", project_payload, 82, "github_rest_v1", "Structured GitHub repository metadata and activity statistics."))
                    claim_rows.append(self._claim(evidence, "technology_adoption", {"market_id": search["market_id"], "company": owner, "technology": ", ".join(repository.get("topics", [])[:12]) or repository.get("language"), "project_url": source_url}, 68, "github_rest_v1", "Repository topics and language support a market-technology association."))
                    claim_rows.append(self._claim(evidence, "company_classification", {"market_id": search["market_id"], "company": owner, "source_url": source_url}, 55, "github_rest_v1", "Repository owner classification requires human confirmation because an owner may be an individual or organization."))
            except Exception as exc:
                query_log.update({"state": "failed", "error": str(exc)})
                run["errors"].append(f"{search['id']}: {exc}")
            query_log["completed_at"] = utc_now()
            run["queries"].append(query_log)
            time.sleep(float(os.getenv("GITHUB_REQUEST_DELAY_SECONDS", "0.15")))
        return self._finish_run(run, raw_rows, evidence_rows, claim_rows)

    def _sec_headers(self) -> dict[str, str]:
        user_agent = os.getenv("SEC_USER_AGENT", "").strip()
        if not user_agent or "YOUR_COMPANY" in user_agent or "your-email" in user_agent.lower():
            raise RuntimeError("SEC_USER_AGENT must identify your organization and a contact email before SEC collection runs.")
        # Let requests derive Host from each official SEC URL.  The collector uses both
        # data.sec.gov and www.sec.gov/files, whose host names differ.
        return {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}

    def _sec_get(self, url: str) -> dict[str, Any]:
        response = requests.get(url, headers=self._sec_headers(), timeout=self.timeout)
        response.raise_for_status()
        time.sleep(float(os.getenv("SEC_REQUEST_DELAY_SECONDS", "0.12")))
        return response.json()

    def resolve_cik(self, company: str, ticker: str | None = None, limit: int = 10) -> dict[str, Any]:
        """Resolve a public filer conservatively against the SEC's official ticker map.

        A result is auto-selected only for an exact ticker or exact normalized company
        name. Partial-name results are returned for a human to choose; no guess becomes
        a collection target.
        """
        company_key = normalized_entity_name(company)
        ticker_key = str(ticker or "").strip().upper()
        if not company_key and not ticker_key:
            raise ValueError("Provide a company name or ticker for SEC CIK resolution.")
        source_url = os.getenv("SEC_COMPANY_TICKERS_URL", "https://www.sec.gov/files/company_tickers.json")
        payload = self._sec_get(source_url)
        records = payload.values() if isinstance(payload, dict) else payload
        matches: list[dict[str, Any]] = []
        for row in records:
            if not isinstance(row, dict) or row.get("cik_str") is None:
                continue
            title = str(row.get("title") or "").strip()
            row_ticker = str(row.get("ticker") or "").strip().upper()
            title_key = normalized_entity_name(title)
            match_kind: str | None = None
            if ticker_key and row_ticker == ticker_key:
                match_kind = "exact_ticker"
            elif company_key and title_key == company_key:
                match_kind = "exact_company_name"
            elif company_key and title_key.startswith(company_key):
                match_kind = "company_name_prefix"
            elif company_key and all(token in title_key.split() for token in company_key.split()):
                match_kind = "company_name_tokens"
            if match_kind:
                matches.append({
                    "cik": normalize_cik(row["cik_str"]),
                    "company": title,
                    "ticker": row_ticker or None,
                    "match_kind": match_kind,
                    "source_url": source_url,
                })
        priority = {"exact_ticker": 0, "exact_company_name": 1, "company_name_prefix": 2, "company_name_tokens": 3}
        matches.sort(key=lambda item: (priority[item["match_kind"]], item["company"]))
        matches = matches[: max(1, min(int(limit), 25))]
        selected = matches[0] if matches and matches[0]["match_kind"] in {"exact_ticker", "exact_company_name"} else None
        return {
            "query": {"company": company or None, "ticker": ticker_key or None},
            "source": {"name": "SEC company_tickers.json", "url": source_url},
            "selected": selected,
            "matches": matches,
            "requires_confirmation": selected is None,
            "note": "Only exact ticker or exact normalized company-name matches are selected automatically; partial matches require operator confirmation.",
        }

    def _resolve_sec_target(self, target: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Fill a target CIK only when the official SEC mapping has an exact match."""
        if target.get("cik"):
            resolved = {**target, "cik": normalize_cik(target["cik"])}
            return resolved, None
        resolution = self.resolve_cik(str(target.get("company") or ""), target.get("ticker"))
        selected = resolution.get("selected")
        if not selected:
            raise ValueError("No exact SEC CIK match. Provide an explicit CIK or choose one of the returned matches before collection.")
        return {**target, "cik": selected["cik"], "company": target.get("company") or selected["company"]}, resolution

    @staticmethod
    def _recent_filings(submissions: dict[str, Any]) -> list[dict[str, Any]]:
        recent = submissions.get("filings", {}).get("recent", {})
        keys = list(recent.keys()) if isinstance(recent, dict) else []
        if not keys:
            return []
        length = max((len(recent.get(key, [])) for key in keys), default=0)
        return [{key: (recent.get(key, [None] * length)[index] if index < len(recent.get(key, [])) else None) for key in keys} for index in range(length)]

    def _sec_revenue_claim(self, evidence: dict[str, Any], company: str, market_id: str, companyfacts: dict[str, Any]) -> dict[str, Any] | None:
        facts = (companyfacts.get("facts") or {}).get("us-gaap") or {}
        concepts = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"]
        for concept in concepts:
            units = (facts.get(concept) or {}).get("units") or {}
            amounts = units.get("USD") or []
            if not amounts:
                continue
            item = max(amounts, key=lambda row: row.get("filed") or "")
            value = item.get("val")
            if value is None:
                continue
            return self._claim(evidence, "revenue_event", {"market_id": market_id, "company": company, "amount_usd": value, "metric": concept, "observed_date": item.get("end") or item.get("filed"), "form": item.get("form"), "period": item.get("fy")}, 88, "sec_edgar_companyfacts_v1", "Structured SEC XBRL company-fact value; financial meaning still requires period-aware review.")
        return None

    def collect_sec_edgar(self, targets: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        section = self.config().get("sec_edgar", {})
        run_id = str(uuid.uuid4())
        run = {"id": run_id, "collector": "sec_edgar", "started_at": utc_now(), "status": "running", "queries": [], "errors": []}
        raw_rows: list[dict[str, Any]] = []
        evidence_rows: list[dict[str, Any]] = []
        claim_rows: list[dict[str, Any]] = []
        if not section.get("enabled", False):
            run["errors"].append("SEC EDGAR collector is disabled in direct_api_criteria.json.")
            return self._finish_run(run, raw_rows, evidence_rows, claim_rows)
        try:
            configured_targets = targets if targets is not None else parse_targets(os.getenv("SEC_EDGAR_CIK_TARGETS"))
        except ValueError as exc:
            run["errors"].append(str(exc))
            return self._finish_run(run, raw_rows, evidence_rows, claim_rows)
        if not configured_targets:
            run["errors"].append("No SEC targets configured. Set SEC_EDGAR_CIK_TARGETS to a JSON array of CIK, market_id, and optional company objects.")
            return self._finish_run(run, raw_rows, evidence_rows, claim_rows)
        allowed_forms = set(section.get("form_types", []))
        lookback = timedelta(days=int(os.getenv("SEC_LOOKBACK_DAYS", "365")))
        cutoff = datetime.now(timezone.utc) - lookback
        for original_target in configured_targets:
            target = original_target
            query_log = {"criterion_id": "sec_edgar", "target": original_target, "started_at": utc_now()}
            try:
                target, resolution = self._resolve_sec_target(original_target)
                if resolution:
                    query_log["cik_resolution"] = resolution
                cik = normalize_cik(target.get("cik", ""))
                submissions_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
                submissions = self._sec_get(submissions_url)
                company = target.get("company") or submissions.get("name") or f"CIK {cik}"
                market_id = target.get("market_id")
                if not market_id:
                    raise ValueError("Each SEC target must include a market_id.")
                filings = [filing for filing in self._recent_filings(submissions) if filing.get("form") in allowed_forms and (date_or_none(filing.get("filingDate")) or cutoff) >= cutoff]
                query_log.update({"state": "succeeded", "results": len(filings), "cik": cik, "company": company})
                for filing in filings:
                    accession = filing.get("accessionNumber") or "unknown"
                    primary_document = filing.get("primaryDocument") or ""
                    cik_number = str(int(cik))
                    source_url = f"https://www.sec.gov/Archives/edgar/data/{cik_number}/{accession.replace('-', '')}/{primary_document}" if primary_document else submissions_url
                    record = {"target": target, "company": company, "cik": cik, "filing": filing, "submissions_url": submissions_url, "run_id": run_id}
                    evidence = self._evidence("sec_edgar", source_url, f"SEC {filing.get('form')} filing: {company}", json.dumps(record, sort_keys=True, default=str), filing.get("filingDate"), run_id, "capital", f"{cik}:{accession}")
                    evidence_rows.append(evidence)
                    raw_rows.append({"id": stable_id("sec_raw", cik, accession), "source": "sec_edgar", "run_id": run_id, "external_id": f"{cik}:{accession}", "retrieved_at": evidence["retrieved_at"], "payload": record})
                    filing_payload = {"market_id": market_id, "company": company, "cik": cik, "form": filing.get("form"), "accession_number": accession, "filed_date": filing.get("filingDate"), "report_date": filing.get("reportDate"), "primary_document": primary_document, "source_url": source_url}
                    claim_rows.append(self._claim(evidence, "sec_filing", filing_payload, 96, "sec_edgar_submissions_v1", "Structured EDGAR submissions metadata."))
                    claim_rows.append(self._claim(evidence, "company_classification", {"market_id": market_id, "company": company, "source_url": source_url}, 75, "sec_edgar_submissions_v1", "Configured CIK-to-market mapping requires classification review."))
                    if filing.get("form") == "D":
                        claim_rows.append(self._claim(evidence, "funding_round", {**filing_payload, "amount_usd": None, "stage": None, "announced_date": filing.get("filingDate")}, 70, "sec_edgar_form_d_v1", "Form D is capital-formation evidence; terms require document-level review before a funding amount is used."))
                if parse_bool(os.getenv("SEC_FETCH_COMPANY_FACTS"), True):
                    companyfacts = self._sec_get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")
                    fact_evidence = self._evidence("sec_edgar", f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json", f"SEC company facts: {company}", json.dumps({"cik": cik, "available_concepts": list((companyfacts.get('facts') or {}).get('us-gaap', {}).keys())[:50]}, sort_keys=True), None, run_id, "revenue", f"{cik}:companyfacts")
                    evidence_rows.append(fact_evidence)
                    raw_rows.append({"id": stable_id("sec_raw", cik, "companyfacts"), "source": "sec_edgar", "run_id": run_id, "external_id": f"{cik}:companyfacts", "retrieved_at": fact_evidence["retrieved_at"], "payload": companyfacts})
                    revenue_claim = self._sec_revenue_claim(fact_evidence, company, market_id, companyfacts)
                    if revenue_claim:
                        claim_rows.append(revenue_claim)
            except Exception as exc:
                query_log.update({"state": "failed", "error": str(exc)})
                run["errors"].append(f"{target}: {exc}")
            query_log["completed_at"] = utc_now()
            run["queries"].append(query_log)
        return self._finish_run(run, raw_rows, evidence_rows, claim_rows)

    def collect(self, collector: str = "all", max_results: int | None = None, targets: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        if collector not in {"github", "sec_edgar", "all"}:
            raise ValueError("collector must be github, sec_edgar, or all")
        reports: list[dict[str, Any]] = []
        if collector in {"github", "all"}:
            reports.append(self.collect_github(max_results=max_results))
        if collector in {"sec_edgar", "all"}:
            reports.append(self.collect_sec_edgar(targets=targets))
        return {
            "collectors": reports,
            "evidence_created": sum(report.get("evidence_created", 0) for report in reports),
            "claims_created": sum(report.get("claims_created", 0) for report in reports),
            "status": "completed" if all(report.get("status") == "completed" for report in reports) else "completed_with_errors",
        }


def get_direct_collectors() -> DirectCollectors:
    return DirectCollectors()
