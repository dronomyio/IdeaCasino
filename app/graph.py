import json
import os
from typing import Any

from neo4j import GraphDatabase


GRAPH_LABELS = [
    "Company", "Round", "Theme", "Subcategory", "VCFirm", "Partner", "News",
    "Market", "CapitalSignal", "BuilderSignal", "DemandSignal", "PainPoint",
    "Infrastructure", "CollectionRun", "SearchCriterion", "Evidence", "Claim",
    "Technology", "Capability", "Solution", "Vendor", "Project", "Builder",
    "AcceleratorCohort", "Buyer", "DemandEvent", "RevenueEvent", "Outcome",
    "MetricSnapshot", "SecFiling", "OntologyConcept",
]


class GraphWriter:
    def __init__(self):
        self.driver = GraphDatabase.driver(
            os.getenv("NEO4J_URI", "bolt://neo4j:7687"),
            auth=(os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "change-me-now")),
        )

    def close(self):
        self.driver.close()

    def ensure_schema(self):
        constraints = [
            "CREATE CONSTRAINT company_name IF NOT EXISTS FOR (n:Company) REQUIRE n.name IS UNIQUE",
            "CREATE CONSTRAINT market_id IF NOT EXISTS FOR (n:Market) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT evidence_id IF NOT EXISTS FOR (n:Evidence) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT claim_id IF NOT EXISTS FOR (n:Claim) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT collection_run_id IF NOT EXISTS FOR (n:CollectionRun) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT funding_round_id IF NOT EXISTS FOR (n:FundingRound) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT metric_snapshot_id IF NOT EXISTS FOR (n:MetricSnapshot) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT sec_filing_id IF NOT EXISTS FOR (n:SecFiling) REQUIRE n.id IS UNIQUE",
        ]
        with self.driver.session() as session:
            for query in constraints:
                session.run(query)

    def ingest(self, rows):
        """Preserve ingestion compatibility for the original curated funding rows."""
        cypher = """
        MERGE (c:Company {name:$company})
        MERGE (r:Round {company:$company, date:$date, round:$round})
        SET r.amount_usd=$amount, r.stage=$stage
        MERGE (c)-[:RAISED]->(r)
        MERGE (t:Theme {name:$theme})
        MERGE (s:Subcategory {name:$subcategory})
        MERGE (s)-[:PART_OF]->(t)
        MERGE (c)-[:CLASSIFIED_AS]->(s)
        WITH c,r,t,s
        UNWIND $investors AS inv
        MERGE (f:VCFirm {name:inv.firm})
        MERGE (p:Partner {name:inv.partner, firm:inv.firm})
        MERGE (p)-[:PARTNER_AT]->(f)
        MERGE (f)-[i:INVESTED_IN {round_date:$date}]->(r)
        SET i.role=inv.role
        MERGE (p)-[:CHAMPIONED]->(r)
        WITH c,r,t,s
        UNWIND $news AS n
        MERGE (a:News {url:n.url})
        SET a.title=n.title, a.source=n.source
        MERGE (a)-[:ABOUT]->(c)
        MERGE (a)-[:MENTIONS_THEME]->(s)
        """
        with self.driver.session() as session:
            for row in rows:
                session.run(
                    cypher,
                    company=row["company"], date=row["date"], round=row["round"], amount=row["amount_usd"],
                    stage=row["stage"], theme=row["theme"], subcategory=row["subcategory"],
                    investors=row.get("investors", []), news=row.get("news", []),
                )

    def ingest_market_profiles(self, markets):
        """Persist the existing Idea Casino market signal model for exploration."""
        cypher = """
        MERGE (m:Market {id:$id})
        SET m.name=$name, m.classification=$classification, m.opportunity_score=$opportunity,
            m.house_edge=$house_edge, m.capital_usd=$capital_usd, m.capital_velocity_90d=$capital_velocity,
            m.active_builders=$active_builders, m.builder_velocity_90d=$builder_velocity,
            m.buyer_demand=$buyer_demand, m.competition_score=$competition_score
        MERGE (t:Theme {name:$theme})
        MERGE (m)-[:PART_OF]->(t)
        MERGE (s:Subcategory {name:$subcategory})
        MERGE (m)-[:CLASSIFIED_AS]->(s)
        MERGE (s)-[:PART_OF]->(t)
        MERGE (capital:CapitalSignal {market_id:$id})
        SET capital.amount_usd=$capital_usd, capital.velocity_90d=$capital_velocity, capital.rounds=$rounds
        MERGE (m)-[:HAS_CAPITAL_SIGNAL]->(capital)
        MERGE (builders:BuilderSignal {market_id:$id})
        SET builders.active=$active_builders, builders.velocity_90d=$builder_velocity, builders.density=$builder_density
        MERGE (m)-[:HAS_BUILDER_SIGNAL]->(builders)
        MERGE (demand:DemandSignal {market_id:$id})
        SET demand.score=$buyer_demand, demand.revenue_evidence=$revenue_evidence, demand.evidence=$demand_evidence
        MERGE (m)-[:HAS_DEMAND_SIGNAL]->(demand)
        MERGE (infra:Infrastructure {market_id:$id})
        SET infra.score=$picks_shovels, infra.dependency=$dependency, infra.adoption=$adoption,
            infra.switching_cost=$switching_cost, infra.monetization=$monetization
        MERGE (m)-[:HAS_PICKS_SHOVELS]->(infra)
        """
        pain_cypher = """
        MATCH (m:Market {id:$market_id})
        MERGE (p:PainPoint {market_id:$market_id, name:$name})
        SET p.capital_exposed_usd=$capital_exposed_usd, p.companies_affected=$companies_affected,
            p.vendors=$vendors, p.urgency=$urgency, p.budget_availability=$budget_availability,
            p.opportunity=$opportunity
        MERGE (m)-[:CREATES_DEMAND_FOR]->(p)
        """
        smart_money_cypher = """
        MATCH (m:Market {id:$market_id})
        MERGE (f:VCFirm {name:$firm})
        MERGE (f)-[:SMART_MONEY_FOR]->(m)
        """
        with self.driver.session() as session:
            for market in markets:
                scores = market.get("scores", {})
                session.run(
                    cypher,
                    id=market["id"], name=market["name"], theme=market["theme"], subcategory=market["subcategory"],
                    classification=market.get("classification", "Watchlist"), opportunity=scores.get("opportunity", 0),
                    house_edge=scores.get("house_edge", 0), capital_usd=market["capital"]["amount_usd"],
                    capital_velocity=market["capital"]["velocity_90d"], rounds=market["capital"]["rounds"],
                    active_builders=market["builders"]["active"], builder_velocity=market["builders"]["velocity_90d"],
                    builder_density=market["builders"]["density"], buyer_demand=market["demand"]["score"],
                    revenue_evidence=market["demand"]["revenue_evidence"], demand_evidence=market["demand"]["evidence"],
                    competition_score=market["competition"]["score"], picks_shovels=scores.get("picks_shovels", market["infrastructure"]["score"]),
                    dependency=market["infrastructure"]["dependency"], adoption=market["infrastructure"]["adoption"],
                    switching_cost=market["infrastructure"]["switching_cost"], monetization=market["infrastructure"]["monetization"],
                )
                for pain in market.get("pain_points", []):
                    session.run(pain_cypher, market_id=market["id"], **pain)
                for investor in market.get("smart_money", []):
                    session.run(smart_money_cypher, market_id=market["id"], firm=investor.split(" · ", 1)[0])

    def ingest_evidence_graph(self, evidence_rows: list[dict[str, Any]], claims: list[dict[str, Any]], runs: list[dict[str, Any]]) -> dict[str, int]:
        """Load raw evidence and all claim states into Neo4j with source provenance."""
        self.ensure_schema()
        evidence_by_id = {row["id"]: row for row in evidence_rows}
        evidence_query = """
        MERGE (e:Evidence {id:$id})
        SET e.url=$url, e.canonical_url=$canonical_url, e.title=$title, e.source_domain=$source_domain,
            e.published_date=$published_date, e.retrieved_at=$retrieved_at, e.criterion_id=$criterion_id,
            e.signal_type=$signal_type, e.source_type=$source_type, e.external_id=$external_id,
            e.tavily_score=$tavily_score, e.content_hash=$content_hash,
            e.content_excerpt=$content_excerpt, e.state=$state
        MERGE (c:SearchCriterion {id:$criterion_id})
        MERGE (c)-[:RETRIEVED]->(e)
        """
        run_query = """
        MERGE (r:CollectionRun {id:$id})
        SET r.started_at=$started_at, r.completed_at=$completed_at, r.status=$status, r.usage_credits=$usage_credits
        """
        claim_query = """
        MATCH (e:Evidence {id:$evidence_id})
        MERGE (c:Claim {id:$id})
        SET c.claim_type=$claim_type, c.payload_json=$payload_json, c.confidence=$confidence,
            c.extraction_method=$extraction_method, c.extraction_reason=$extraction_reason,
            c.evidence_type=$evidence_type, c.provenance=$provenance, c.validation_status=$validation_status,
            c.observed_at=$observed_at, c.state=$state, c.review_required=$review_required,
            c.reviewed_at=$reviewed_at, c.reviewed_by=$reviewed_by, c.review_note=$review_note
        MERGE (e)-[:SUPPORTS]->(c)
        """
        run_link_query = """
        MATCH (r:CollectionRun {id:$run_id}), (e:Evidence {id:$evidence_id})
        MERGE (r)-[:RETRIEVED]->(e)
        """
        with self.driver.session() as session:
            for run in runs:
                session.run(run_query, **run)
            for evidence in evidence_rows:
                session.run(evidence_query, **evidence)
                run_id = (evidence.get("raw_reference") or {}).get("run_id")
                if run_id:
                    session.run(run_link_query, run_id=run_id, evidence_id=evidence["id"])
            for claim in claims:
                session.run(claim_query, **{**claim, "payload_json": json.dumps(claim.get("payload", {}), sort_keys=True), "evidence_type": claim.get("evidence_type", "SOURCE_FACT"), "provenance": claim.get("provenance", "source_fact"), "validation_status": (claim.get("validation") or {}).get("status")})
                source_fact_trusted = claim.get("state") in {"verified", "manual_confirmed"} and claim.get("evidence_type", "SOURCE_FACT") in {"SOURCE_FACT", "MODEL_CLASSIFICATION"}
                inference_accepted = claim.get("state") == "accepted_inference" and claim.get("evidence_type") == "MODEL_INFERENCE"
                if source_fact_trusted or inference_accepted:
                    self._project_claim(session, claim)
        return {"evidence": len(evidence_rows), "claims": len(claims), "runs": len(runs)}

    @staticmethod
    def _project_claim(session, claim: dict[str, Any]) -> None:
        """Create domain nodes/edges from a claim while retaining the source Claim node."""
        payload: dict[str, Any] = claim.get("payload") or {}
        claim_id = claim["id"]
        company = payload.get("company")
        market_id = payload.get("market_id")
        if company:
            session.run("MERGE (co:Company {name:$company}) WITH co MATCH (c:Claim {id:$claim_id}) MERGE (c)-[:ABOUT]->(co)", company=company, claim_id=claim_id)
        if market_id:
            session.run("MERGE (m:Market {id:$market_id}) ON CREATE SET m.name=$market_id WITH m MATCH (c:Claim {id:$claim_id}) MERGE (c)-[:MEASURES]->(m)", market_id=market_id, claim_id=claim_id)
            if company:
                session.run("MATCH (co:Company {name:$company}), (m:Market {id:$market_id}) MERGE (co)-[:CLASSIFIED_AS {claim_id:$claim_id}]->(m)", company=company, market_id=market_id, claim_id=claim_id)
        claim_type = claim.get("claim_type")
        if claim_type == "funding_round" and company:
            round_id = f"{company}|{payload.get('announced_date')}|{payload.get('stage')}"
            session.run("""
                MATCH (co:Company {name:$company}), (c:Claim {id:$claim_id})
                MERGE (r:FundingRound {id:$round_id})
                SET r.amount_usd=$amount_usd, r.stage=$stage, r.announced_date=$announced_date
                MERGE (co)-[:RAISED]->(r)
                MERGE (c)-[:ASSERTS]->(r)
            """, company=company, claim_id=claim_id, round_id=round_id, amount_usd=payload.get("amount_usd"), stage=payload.get("stage"), announced_date=payload.get("announced_date"))
        elif claim_type == "investor_participation" and company:
            firm = payload.get("firm")
            if firm:
                round_id = f"{company}|{payload.get('round_date')}|{payload.get('stage')}"
                session.run("""
                    MATCH (co:Company {name:$company}), (c:Claim {id:$claim_id})
                    MERGE (f:InvestorFirm {name:$firm})
                    MERGE (r:FundingRound {id:$round_id})
                    SET r.announced_date=$round_date, r.stage=$stage
                    MERGE (co)-[:RAISED]->(r)
                    MERGE (f)-[i:INVESTED_IN]->(r)
                    SET i.role=$role, i.claim_id=$claim_id
                    MERGE (c)-[:ASSERTS]->(f)
                """, company=company, claim_id=claim_id, firm=firm, round_id=round_id, round_date=payload.get("round_date"), stage=payload.get("stage"), role=payload.get("role"))
        elif claim_type == "partner_participation" and company:
            firm, partner = payload.get("firm"), payload.get("partner")
            if firm and partner:
                round_id = f"{company}|{payload.get('round_date')}|{payload.get('stage')}"
                session.run("""
                    MATCH (co:Company {name:$company}), (c:Claim {id:$claim_id})
                    MERGE (f:InvestorFirm {name:$firm})
                    MERGE (p:Partner {id:$partner_id})
                    SET p.name=$partner, p.firm=$firm
                    MERGE (p)-[:PARTNER_AT]->(f)
                    MERGE (r:FundingRound {id:$round_id})
                    SET r.announced_date=$round_date, r.stage=$stage
                    MERGE (co)-[:RAISED]->(r)
                    MERGE (p)-[i:CHAMPIONED]->(r)
                    SET i.role=$role, i.claim_id=$claim_id
                    MERGE (c)-[:ASSERTS]->(p)
                """, company=company, claim_id=claim_id, firm=firm, partner=partner, partner_id=f"{firm}|{partner}", round_id=round_id, round_date=payload.get("round_date"), stage=payload.get("stage"), role=payload.get("role"))
        elif claim_type == "ontology_relationship" and company:
            relationship = payload.get("relationship")
            source_fact_allowed = {"USES", "NEEDS", "SERVES", "SOLVES", "CLASSIFIED_AS", "HAS_APPLICATION", "INVESTED_IN", "LED", "PARTNER_AT", "RAISED", "HAS_TECHNOLOGY"}
            inference_allowed = {"LIKELY_NEEDS", "LIKELY_SERVES", "LIKELY_SOLVES", "LIKELY_DEPENDS_ON", "OPPORTUNITY_FOR"}
            evidence_type = claim.get("evidence_type", "SOURCE_FACT")
            state = claim.get("state")
            allowed = relationship in source_fact_allowed if evidence_type != "MODEL_INFERENCE" else relationship in inference_allowed
            admitted = (evidence_type != "MODEL_INFERENCE" and state in {"verified", "manual_confirmed"}) or (evidence_type == "MODEL_INFERENCE" and state == "accepted_inference")
            target = payload.get("target")
            if allowed and admitted and target:
                kind = "technology" if relationship in {"USES", "HAS_TECHNOLOGY"} else ("dependency" if "NEEDS" in relationship or "DEPENDS" in relationship else "application")
                query = f"""
                    MATCH (co:Company {{name:$company}}), (c:Claim {{id:$claim_id}})
                    MERGE (target:OntologyConcept {{name:$target, kind:$kind}})
                    MERGE (co)-[r:{relationship}]->(target)
                    SET r.claim_id=$claim_id, r.evidence_type=$evidence_type, r.provenance=$provenance,
                        r.confidence=$confidence, r.literal_quote=$literal_quote, r.rationale=$rationale
                    MERGE (c)-[:ASSERTS]->(target)
                """
                session.run(query, company=company, claim_id=claim_id, target=target, kind=kind, evidence_type=evidence_type, provenance=claim.get("provenance"), confidence=payload.get("inference_confidence") or claim.get("confidence"), literal_quote=payload.get("literal_quote"), rationale=payload.get("rationale"))
        elif claim_type == "sec_filing" and company:
            filing_id = f"{payload.get('cik')}|{payload.get('accession_number')}"
            session.run("""
                MATCH (co:Company {name:$company}), (c:Claim {id:$claim_id})
                MERGE (f:SecFiling {id:$filing_id})
                SET f.cik=$cik, f.accession_number=$accession_number, f.form=$form,
                    f.filed_date=$filed_date, f.report_date=$report_date, f.source_url=$source_url
                MERGE (co)-[:FILED]->(f)
                MERGE (c)-[:ASSERTS]->(f)
            """, company=company, claim_id=claim_id, filing_id=filing_id, cik=payload.get("cik"), accession_number=payload.get("accession_number"), form=payload.get("form"), filed_date=payload.get("filed_date"), report_date=payload.get("report_date"), source_url=payload.get("source_url"))
        elif claim_type in {"builder_project", "builder_company"}:
            project_url = payload.get("project_url") or payload.get("source_url")
            if project_url:
                session.run("""
                    MATCH (c:Claim {id:$claim_id})
                    MERGE (p:Project {url:$url})
                    SET p.observed_date=$observed_date, p.repository=$repository, p.stars=$stars, p.forks=$forks,
                        p.open_issues=$open_issues, p.language=$language, p.topics=$topics, p.created_at=$created_at,
                        p.pushed_at=$pushed_at, p.recent_commit_count=$recent_commit_count, p.contributor_count=$contributor_count
                    MERGE (c)-[:ASSERTS]->(p)
                """, claim_id=claim_id, url=project_url, observed_date=payload.get("observed_date") or payload.get("pushed_at"), repository=payload.get("repository"), stars=payload.get("stars"), forks=payload.get("forks"), open_issues=payload.get("open_issues"), language=payload.get("language"), topics=payload.get("topics", []), created_at=payload.get("created_at"), pushed_at=payload.get("pushed_at"), recent_commit_count=payload.get("recent_commit_count"), contributor_count=payload.get("contributor_count"))
                if company:
                    session.run("MATCH (p:Project {url:$url}) MERGE (b:Builder {id:$builder_id}) SET b.name=$builder_name MERGE (b)-[:BUILDS]->(p)", url=project_url, builder_id=company, builder_name=company)
                if market_id:
                    session.run("MATCH (p:Project {url:$url}), (m:Market {id:$market_id}) MERGE (p)-[:TARGETS]->(m)", url=project_url, market_id=market_id)
        elif claim_type in {"job_posting", "procurement_event", "customer_adoption"}:
            session.run("MATCH (c:Claim {id:$claim_id}) MERGE (d:DemandEvent {id:$claim_id}) SET d.type=$claim_type, d.observed_date=$observed_date, d.amount_usd=$amount_usd MERGE (c)-[:ASSERTS]->(d)", claim_id=claim_id, claim_type=claim_type, observed_date=payload.get("observed_date"), amount_usd=payload.get("amount_usd"))
            if market_id:
                session.run("MATCH (d:DemandEvent {id:$claim_id}), (m:Market {id:$market_id}) MERGE (d)-[:DEMANDS]->(m)", claim_id=claim_id, market_id=market_id)
        elif claim_type == "revenue_event" and company:
            session.run("MATCH (co:Company {name:$company}), (c:Claim {id:$claim_id}) MERGE (r:RevenueEvent {id:$claim_id}) SET r.amount_usd=$amount_usd, r.metric=$metric, r.observed_date=$observed_date MERGE (co)-[:REPORTED]->(r) MERGE (c)-[:ASSERTS]->(r)", company=company, claim_id=claim_id, amount_usd=payload.get("amount_usd"), metric=payload.get("metric"), observed_date=payload.get("observed_date"))
        elif claim_type == "outcome_event" and company:
            session.run("MATCH (co:Company {name:$company}), (c:Claim {id:$claim_id}) MERGE (o:Outcome {id:$claim_id}) SET o.type=$outcome_type, o.observed_date=$observed_date MERGE (co)-[:HAD_OUTCOME]->(o) MERGE (c)-[:ASSERTS]->(o)", company=company, claim_id=claim_id, outcome_type=payload.get("outcome_type"), observed_date=payload.get("observed_date"))
        elif claim_type == "technology_dependency" and market_id:
            pain = payload.get("pain_point") or "Unspecified dependency"
            session.run("MATCH (m:Market {id:$market_id}), (c:Claim {id:$claim_id}) MERGE (p:PainPoint {id:$pain_id}) SET p.name=$pain MERGE (m)-[:CREATES_DEMAND_FOR {claim_id:$claim_id}]->(p) MERGE (c)-[:ASSERTS]->(p)", market_id=market_id, claim_id=claim_id, pain_id=f"{market_id}|{pain}", pain=pain)

    def ingest_metric_snapshots(self, snapshots: list[dict[str, Any]]) -> int:
        self.ensure_schema()
        query = """
        MERGE (s:MetricSnapshot {id:$id})
        SET s.metric=$metric, s.value=$value, s.unit=$unit, s.status=$status, s.formula_version=$formula_version,
            s.window_start=$window_start, s.window_end=$window_end, s.calculated_at=$calculated_at,
            s.input_count=$input_count, s.input_ids_json=$input_ids_json, s.detail_json=$detail_json
        MERGE (m:Market {id:$market_id})
        MERGE (s)-[:MEASURES]->(m)
        """
        with self.driver.session() as session:
            for snapshot in snapshots:
                session.run(query, **{**snapshot, "input_ids_json": json.dumps(snapshot.get("input_ids", [])), "detail_json": json.dumps(snapshot.get("detail", {}))})
        return len(snapshots)

    def idea_context(self, terms: list[str], market_ids: list[str], limit: int = 30) -> list[dict[str, Any]]:
        """Return a bounded, source-linked Neo4j context for an LLM idea query."""
        query = """
        MATCH (e:Evidence)-[:SUPPORTS]->(c:Claim)
        OPTIONAL MATCH (c)-[:MEASURES]->(m:Market)
        OPTIONAL MATCH (c)-[:ABOUT|ASSERTS]->(entity)
        WHERE ($market_ids = [] OR m.id IN $market_ids OR c.payload_json CONTAINS any_market_placeholder)
          AND ($terms = [] OR any(term IN $terms WHERE toLower(coalesce(e.title, '')) CONTAINS toLower(term) OR toLower(coalesce(e.content_excerpt, '')) CONTAINS toLower(term) OR toLower(coalesce(c.payload_json, '')) CONTAINS toLower(term)))
        RETURN e.id AS evidence_id, e.title AS title, e.url AS url, e.published_date AS published_date,
               e.source_type AS source_type, c.id AS claim_id, c.claim_type AS claim_type, c.state AS state,
               c.confidence AS confidence, c.payload_json AS payload_json, m.id AS market_id,
               coalesce(entity.name, entity.id, entity.url) AS entity
        ORDER BY e.published_date DESC, c.confidence DESC
        LIMIT $limit
        """
        # Cypher does not support a parameter inside CONTAINS as a collection predicate;
        # the market filter is therefore handled locally after retrieval while term filtering stays parameterized.
        query = query.replace("($market_ids = [] OR m.id IN $market_ids OR c.payload_json CONTAINS any_market_placeholder)", "true")
        records: list[dict[str, Any]] = []
        with self.driver.session() as session:
            for row in session.run(query, terms=terms, limit=limit * 3):
                payload = row["payload_json"] or "{}"
                try:
                    payload_data = json.loads(payload)
                except (TypeError, json.JSONDecodeError):
                    payload_data = {}
                row_market = row["market_id"] or payload_data.get("market_id")
                if market_ids and row_market not in market_ids:
                    continue
                records.append({
                    "evidence_id": row["evidence_id"], "title": row["title"], "url": row["url"], "published_date": row["published_date"],
                    "source_type": row["source_type"], "claim_id": row["claim_id"], "claim_type": row["claim_type"],
                    "state": row["state"], "confidence": row["confidence"], "payload": payload_data,
                    "market_id": row_market, "entity": row["entity"],
                })
                if len(records) >= limit:
                    break
        return records

    def graph_json(self):
        labels = ",".join(f"'{label}'" for label in GRAPH_LABELS)
        query = f"""
        MATCH (a)-[r]->(b)
        WHERE any(lbl IN labels(a) WHERE lbl IN [{labels}])
          AND any(lbl IN labels(b) WHERE lbl IN [{labels}])
        RETURN elementId(a) aid, labels(a)[0] alabel, coalesce(a.name, a.title, a.company, a.id, a.market_id, a.url) aname,
               type(r) rel, elementId(b) bid, labels(b)[0] blabel, coalesce(b.name, b.title, b.company, b.id, b.market_id, b.url) bname,
               coalesce(b.amount_usd, a.amount_usd, b.capital_usd, a.capital_usd, b.value, a.value, b.opportunity, a.opportunity, 0) amount
        LIMIT 3000
        """
        nodes, edges = {}, []
        with self.driver.session() as session:
            for record in session.run(query):
                nodes[record["aid"]] = {"id": record["aid"], "label": record["aname"], "type": record["alabel"], "amount": record["amount"] or 0}
                nodes[record["bid"]] = {"id": record["bid"], "label": record["bname"], "type": record["blabel"], "amount": record["amount"] or 0}
                edges.append({"from": record["aid"], "to": record["bid"], "label": record["rel"]})
        return {"nodes": list(nodes.values()), "edges": edges}
