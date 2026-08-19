from pathlib import Path
import json
import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .core_intelligence import get_core_intelligence
from .direct_collectors import get_direct_collectors
from .evidence_engine import get_evidence_engine
from .graph import GraphWriter
from .idea_research import get_idea_research
from .llm_enrichment import get_llm_enricher
from .live_projection import get_live_market_projection
from .live_tabs import get_live_tab_projection
from .validator import get_candidate_validator
from .market import get_market_intelligence
from .metrics_engine import get_metrics_engine
from .storage import get_store
from .tavily_ingest import TavilyIngestor

load_dotenv()
app = FastAPI(
    title="Idea Casino",
    version="2.0.0",
    description="Evidence-led market intelligence for builders, operators, and investors.",
)
HTML = Path(__file__).parent / "static" / "index.html"


class IdeaQuery(BaseModel):
    query: str = Field(min_length=2, max_length=280)


class CollectionRequest(BaseModel):
    criteria_ids: list[str] | None = None
    max_queries: int | None = Field(default=None, ge=1, le=100)


class ClaimReview(BaseModel):
    decision: str = Field(pattern="^(verified|rejected|manual_confirmed|accepted_inference)$")
    reviewer: str = Field(default="operator", min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=2000)
    payload_patch: dict | None = None


class MetricCalculationRequest(BaseModel):
    window_days: int | None = Field(default=None, ge=7, le=730)
    as_of: str | None = Field(default=None, pattern="^\\d{4}-\\d{2}-\\d{2}$")


class DirectCollectionRequest(BaseModel):
    collector: str = Field(default="all", pattern="^(github|sec_edgar|all)$")
    max_results: int | None = Field(default=None, ge=1, le=100)
    targets: list[dict] | None = None


class CikResolutionRequest(BaseModel):
    company: str = Field(default="", max_length=240)
    ticker: str | None = Field(default=None, max_length=16)
    limit: int = Field(default=10, ge=1, le=25)


class LLMEnrichmentRequest(BaseModel):
    evidence_ids: list[str] | None = None
    limit: int | None = Field(default=None, ge=1, le=100)


class IdeaResearchRequest(BaseModel):
    idea: str = Field(min_length=3, max_length=1000)
    mode: str = Field(default="local_first", pattern="^(local_only|local_first|live_refresh)$")


class CandidateValidationRequest(BaseModel):
    claim_ids: list[str] | None = None
    limit: int | None = Field(default=None, ge=1, le=500)


class CoreIdeaCheckRequest(BaseModel):
    idea: str = Field(min_length=3, max_length=1000)
    geography: str | None = Field(default=None, max_length=120)
    stage: str | None = Field(default=None, max_length=80)
    include_demo_profiles: bool = True


@app.get("/", response_class=HTMLResponse)
def home():
    return HTML.read_text()


# --- Idea Casino product APIs -------------------------------------------------
@app.get("/api/dashboard")
def dashboard():
    """Homepage scorecard based only on the stored market-signal profiles."""
    return get_market_intelligence().dashboard()


@app.get("/api/live-dashboard")
def live_dashboard():
    """Landing-page projection using reviewed evidence and computed metrics only."""
    return get_live_market_projection().dashboard()


@app.get("/api/live-markets")
def live_markets():
    """Market-table payload using reviewed evidence and deterministic snapshots only."""
    return get_live_tab_projection().markets()


@app.get("/api/live-markets/{market_id}/money-path")
def live_money_path(market_id: str):
    """Reviewed-evidence money-flow payload; no seeded allocations or scores."""
    result = get_live_tab_projection().money_path(market_id)
    if not result:
        raise HTTPException(404, "Market not found")
    return result


@app.get("/api/live-markets/{market_id}/pain-graph")
def live_pain_graph(market_id: str):
    """Reviewed-evidence pain payload; values remain absent until computed."""
    result = get_live_tab_projection().pain_graph(market_id)
    if not result:
        raise HTTPException(404, "Market not found")
    return result


@app.get("/api/live-market-graph")
def live_market_graph():
    """Taxonomy and reviewed-evidence graph that excludes demonstration profiles."""
    return get_live_tab_projection().market_graph()


@app.get("/api/markets")
def markets(
    status: str | None = Query(default=None),
    q: str | None = Query(default=None, max_length=120),
):
    return get_market_intelligence().markets(status=status, term=q)


@app.get("/api/markets/{market_id}/money-path")
def money_path(market_id: str):
    result = get_market_intelligence().money_path(market_id)
    if not result:
        raise HTTPException(404, "Market not found")
    return result


@app.get("/api/markets/{market_id}/pain-graph")
def pain_graph(market_id: str):
    result = get_market_intelligence().pain_graph(market_id)
    if not result:
        raise HTTPException(404, "Market not found")
    return result


@app.get("/api/markets/{market_id}")
def market_detail(market_id: str):
    result = get_market_intelligence().market(market_id)
    if not result:
        raise HTTPException(404, "Market not found")
    return result


@app.post("/api/idea-odds")
def idea_odds(payload: IdeaQuery):
    try:
        return get_market_intelligence().idea_odds(payload.query)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/idea-research/policy")
def idea_research_policy():
    return get_idea_research().policy


@app.post("/api/idea-research")
def idea_research(payload: IdeaResearchRequest):
    try:
        return get_idea_research().query(payload.idea, payload.mode)
    except Exception as exc:
        raise HTTPException(503, f"Idea research unavailable: {exc}") from exc


@app.post("/api/intelligence/ingest")
def ingest_market_intelligence():
    """Load the stored Idea Casino signal model into Neo4j for graph exploration."""
    market_profiles = get_market_intelligence().markets()
    try:
        graph_writer = GraphWriter()
        try:
            graph_writer.ingest_market_profiles(market_profiles)
        finally:
            graph_writer.close()
        return {
            "ok": True,
            "markets": len(market_profiles),
            "note": "Market, signal, smart-money, and pain-point nodes were upserted into Neo4j.",
        }
    except Exception as exc:
        raise HTTPException(503, f"Neo4j market ingestion unavailable: {exc}") from exc


@app.get("/api/market-graph")
def market_graph():
    return get_market_intelligence().market_graph()


@app.get("/api/methodology")
def methodology():
    return get_market_intelligence().methodology()


# --- Core Intelligence API (read-only shared service boundary) ----------------
@app.get("/api/core/catalog")
def core_catalog():
    return get_core_intelligence().catalog()


@app.get("/api/core/funding")
def core_funding(theme: str | None = None, days: int = Query(default=365, ge=1, le=3650), limit: int = Query(default=50, ge=1, le=200)):
    return get_core_intelligence().search_funding(theme=theme, days=days, limit=limit)


@app.get("/api/core/investors/{company}")
def core_investor_graph(company: str, include_inferences: bool = False):
    return get_core_intelligence().get_investor_graph(company, include_inferences=include_inferences)


@app.get("/api/core/partners/{partner}")
def core_partner_activity(partner: str, days: int = Query(default=365, ge=1, le=3650)):
    return get_core_intelligence().get_partner_activity(partner, days=days)


@app.get("/api/core/compare-builder-capital")
def core_compare_builder_capital(theme: str, include_demo_profiles: bool = True):
    return get_core_intelligence().compare_builder_vs_capital(theme, include_demo_profiles=include_demo_profiles)


@app.get("/api/core/underbuilt")
def core_underbuilt(limit: int = Query(default=10, ge=1, le=50), include_demo_profiles: bool = True):
    return get_core_intelligence().find_underbuilt_markets(limit=limit, include_demo_profiles=include_demo_profiles)


@app.get("/api/core/pain-graph")
def core_pain_graph(theme: str, limit: int = Query(default=10, ge=1, le=50), include_demo_profiles: bool = True):
    return get_core_intelligence().get_pain_graph(theme, limit=limit, include_demo_profiles=include_demo_profiles)


@app.post("/api/core/check-idea")
def core_check_idea(payload: CoreIdeaCheckRequest):
    return get_core_intelligence().check_idea(payload.idea, payload.geography, payload.stage, payload.include_demo_profiles)


@app.post("/api/core/explain-idea")
def core_explain_idea(payload: CoreIdeaCheckRequest):
    return get_core_intelligence().explain_idea_odds(payload.idea, payload.include_demo_profiles)


# --- Evidence engine APIs -----------------------------------------------------
@app.get("/api/evidence/summary")
def evidence_summary():
    return get_evidence_engine().summary()


@app.get("/api/evidence/criteria")
def evidence_criteria(enabled_only: bool = Query(default=False)):
    engine = get_evidence_engine()
    return {"defaults": engine.criteria_config().get("collection_defaults", {}), "criteria": engine.criteria(enabled_only=enabled_only)}


@app.post("/api/evidence/collect")
def evidence_collect(payload: CollectionRequest):
    try:
        return get_evidence_engine().collect(payload.criteria_ids, payload.max_queries)
    except Exception as exc:
        raise HTTPException(503, f"Evidence collection unavailable: {exc}") from exc


@app.get("/api/evidence")
def evidence_list(state: str | None = None, criterion_id: str | None = None):
    return get_evidence_engine().list_evidence(state=state, criterion_id=criterion_id)


@app.get("/api/direct-collectors/config")
def direct_collectors_config():
    return get_direct_collectors().config()


@app.post("/api/direct-collectors/collect")
def direct_collectors_collect(payload: DirectCollectionRequest):
    try:
        return get_direct_collectors().collect(payload.collector, payload.max_results, payload.targets)
    except Exception as exc:
        raise HTTPException(503, f"Direct collection unavailable: {exc}") from exc


@app.post("/api/sec-edgar/resolve-cik")
def resolve_sec_cik(payload: CikResolutionRequest):
    """Resolve a name or ticker using the SEC's official company ticker map.

    Only exact results are selected automatically; ambiguous candidates are returned for
    operator confirmation and are never collected implicitly.
    """
    try:
        return get_direct_collectors().resolve_cik(payload.company, payload.ticker, payload.limit)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, f"SEC CIK resolution unavailable: {exc}") from exc


@app.post("/api/evidence/llm-enrich")
def evidence_llm_enrich(payload: LLMEnrichmentRequest):
    try:
        return get_llm_enricher().enrich(payload.evidence_ids, payload.limit)
    except Exception as exc:
        raise HTTPException(503, f"LLM enrichment unavailable: {exc}") from exc


@app.get("/api/claims")
def claims_list(state: str | None = None, claim_type: str | None = None):
    return get_evidence_engine().list_claims(state=state, claim_type=claim_type)


@app.post("/api/claims/validate")
def validate_claims(payload: CandidateValidationRequest):
    return get_candidate_validator().validate_pending(payload.claim_ids, payload.limit)


@app.post("/api/claims/{claim_id}/review")
def claim_review(claim_id: str, payload: ClaimReview):
    try:
        return get_evidence_engine().review_claim(claim_id, payload.decision, payload.reviewer, payload.note, payload.payload_patch)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/collection-runs")
def collection_runs():
    return sorted(get_evidence_engine().runs.load(), key=lambda row: row.get("started_at", ""), reverse=True)


@app.post("/api/evidence/graph/ingest")
def ingest_evidence_graph():
    engine = get_evidence_engine()
    try:
        graph_writer = GraphWriter()
        try:
            return {"ok": True, **graph_writer.ingest_evidence_graph(engine.evidence.load(), engine.claims.load(), engine.runs.load())}
        finally:
            graph_writer.close()
    except Exception as exc:
        raise HTTPException(503, f"Evidence graph ingestion unavailable: {exc}") from exc


# --- Exact calculation engine APIs -------------------------------------------
@app.get("/api/calculations/definitions")
def calculation_definitions():
    return get_metrics_engine().definitions()


@app.post("/api/metrics/calculate")
def calculate_metrics(payload: MetricCalculationRequest):
    return {"snapshots": get_metrics_engine().calculate(payload.window_days, payload.as_of)}


@app.get("/api/metrics")
def metric_snapshots(market_id: str | None = None, status: str | None = None):
    return get_metrics_engine().list_snapshots(market_id, status)


@app.get("/api/metrics/{market_id}/drilldown")
def metric_drilldown(market_id: str):
    return get_metrics_engine().drilldown(market_id)


@app.post("/api/metrics/graph/ingest")
def ingest_metric_graph():
    engine = get_metrics_engine()
    snapshots = engine.list_snapshots()
    try:
        graph_writer = GraphWriter()
        try:
            count = graph_writer.ingest_metric_snapshots(snapshots)
        finally:
            graph_writer.close()
        return {"ok": True, "snapshots": count}
    except Exception as exc:
        raise HTTPException(503, f"Metric graph ingestion unavailable: {exc}") from exc


# --- Existing VC Flow data collection and graph APIs ------------------------
@app.get("/api/funding")
def funding():
    return get_store().load()


@app.get("/api/sources")
def sources():
    path = Path(os.getenv("SOURCES_FILE", "/app/data/sources.json"))
    return json.loads(path.read_text()) if path.exists() else {}


@app.get("/api/candidates")
def candidates(review_required: bool | None = Query(default=None)):
    path = Path(os.getenv("TAVILY_CANDIDATES_FILE", "/app/data/tavily_candidates.json"))
    if not path.exists():
        return []
    rows = json.loads(path.read_text())
    if review_required is None:
        return rows
    return [row for row in rows if bool(row.get("review_required")) == review_required]


@app.post("/api/tavily/collect")
def tavily_collect():
    try:
        return {"ok": True, **TavilyIngestor().collect()}
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc


@app.post("/api/ingest")
def ingest(source: str = Query(default="file", pattern="^(file|tavily_candidates)$")):
    rows = get_store(source).load()
    graph_writer = GraphWriter()
    try:
        graph_writer.ingest(rows)
    except Exception as exc:
        raise HTTPException(503, f"Neo4j ingestion unavailable: {exc}") from exc
    finally:
        graph_writer.close()
    return {"ok": True, "records": len(rows), "source": source}


@app.post("/api/tavily/collect-and-ingest")
def collect_and_ingest():
    try:
        report = TavilyIngestor().collect()
        rows = get_store("tavily_candidates").load()
        graph_writer = GraphWriter()
        try:
            graph_writer.ingest(rows)
        finally:
            graph_writer.close()
        return {
            "ok": True,
            **report,
            "ingested": len(rows),
            "note": "Only high-confidence candidates are automatically ingested; review /api/candidates for the rest.",
        }
    except Exception as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/graph")
def graph():
    try:
        graph_writer = GraphWriter()
        try:
            return {"source": "neo4j", **graph_writer.graph_json()}
        finally:
            graph_writer.close()
    except Exception as exc:
        # The market graph remains usable even if the optional Neo4j relationship
        # store has not been started or populated yet.
        return {"source": "neo4j", "nodes": [], "edges": [], "warning": str(exc)}
