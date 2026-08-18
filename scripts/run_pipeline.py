#!/usr/bin/env python3
"""Run the configured Idea Casino collection pipeline.

Designed for CI or manual execution. All sources write through the same evidence,
claim, review, metric, and graph pipeline. No collector is assumed to be configured.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from app.direct_collectors import get_direct_collectors
from app.evidence_engine import JsonList, get_evidence_engine
from app.graph import GraphWriter
from app.llm_enrichment import get_llm_enricher
from app.metrics_engine import get_metrics_engine
from app.validator import get_candidate_validator


def as_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_step(report: dict, name: str, callback):
    step = {"name": name, "started_at": now()}
    try:
        result = callback()
        step.update({"status": "completed", "result": result})
    except Exception as exc:
        step.update({"status": "failed", "error": str(exc)})
        report["errors"].append(f"{name}: {exc}")
    step["completed_at"] = now()
    report["steps"].append(step)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the configured Idea Casino data pipeline.")
    parser.add_argument("--strict", action="store_true", help="Return a non-zero exit code if any enabled step fails.")
    parser.add_argument("--skip-tavily", action="store_true")
    parser.add_argument("--skip-github", action="store_true")
    parser.add_argument("--skip-sec", action="store_true")
    parser.add_argument("--skip-llm", action="store_true")
    parser.add_argument("--skip-graph", action="store_true")
    args = parser.parse_args()

    report = {"id": str(uuid.uuid4()), "pipeline": "idea_casino_unified_v1", "started_at": now(), "steps": [], "errors": []}
    evidence_engine = get_evidence_engine()
    direct = get_direct_collectors()

    if as_bool("PIPELINE_TAVILY_ENABLED", True) and not args.skip_tavily:
        run_step(report, "tavily_web_research", lambda: evidence_engine.collect())
    if as_bool("PIPELINE_GITHUB_ENABLED", True) and not args.skip_github:
        run_step(report, "github_builder_flow", lambda: direct.collect("github"))
    if as_bool("PIPELINE_SEC_ENABLED", True) and not args.skip_sec:
        run_step(report, "sec_edgar_capital_flow", lambda: direct.collect("sec_edgar"))
    if as_bool("PIPELINE_LLM_ENABLED", False) and not args.skip_llm:
        run_step(report, "ontology_enrichment", lambda: get_llm_enricher().enrich())
    if as_bool("PIPELINE_AUTO_VALIDATION", True):
        run_step(report, "deterministic_validation", lambda: get_candidate_validator().validate_pending(limit=int(os.getenv("VALIDATION_MAX_CANDIDATES_PER_RUN", "500"))))
    if as_bool("PIPELINE_AUTO_METRIC_CALCULATION", True):
        run_step(report, "metric_calculation", lambda: {"snapshots": len(get_metrics_engine().calculate(int(os.getenv("PIPELINE_METRIC_WINDOW_DAYS", "90"))))})
    if as_bool("PIPELINE_AUTO_GRAPH_SYNC", True) and not args.skip_graph:
        def graph_sync():
            writer = GraphWriter()
            try:
                evidence_result = writer.ingest_evidence_graph(evidence_engine.evidence.load(), evidence_engine.claims.load(), evidence_engine.runs.load())
                metric_result = writer.ingest_metric_snapshots(get_metrics_engine().list_snapshots())
                return {"evidence_graph": evidence_result, "metric_snapshots": metric_result}
            finally:
                writer.close()
        run_step(report, "neo4j_sync", graph_sync)

    report["completed_at"] = now()
    report["status"] = "completed" if not report["errors"] else "completed_with_errors"
    data_dir = Path(os.getenv("DATA_DIR", str(ROOT / "data")))
    reports = JsonList(Path(os.getenv("PIPELINE_REPORTS_FILE", str(data_dir / "pipeline_reports.json"))))
    reports.upsert([report])
    print(json.dumps(report, indent=2, default=str))
    return 1 if args.strict and report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
