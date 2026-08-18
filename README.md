# Idea Casino

> **See the odds before you build.**

Idea Casino is a Dockerized market-intelligence application. It now includes an **evidence-backed data engine** that collects public signals with Tavily, retains source-level evidence, creates reviewable claims, computes versioned market metrics only from approved evidence, and loads the full relationship model into Neo4j.

The product is an information and discovery system, not personalized investment advice and not a startup-outcome prediction engine. It measures observable conditions; it does not claim that a high score guarantees a successful company or investment.

## Evidence-first operating rule

> **No source, no score.** A Tavily result is not automatically treated as a market fact. It is retained as immutable source evidence, normalized into a typed claim, reviewed, and only then eligible for calculations.

The collection engine uses Tavily Search with configured query, topic, domain, date-window, raw-content, relevance, retry, and usage controls. Tavily supports domain filters, date filtering, raw content retrieval, usage reporting, and result relevance scores; the engine records those returned details alongside every source.[1] [2]

```text
Search criterion
      │
      ▼
Tavily Search request + collection run
      │
      ▼
Canonical source evidence
URL · title · source domain · content hash · published date · relevance · raw reference
      │
      ▼
Normalized typed claim
payload · confidence · extraction rationale · review state
      │
      ├── review_required / rejected ──► retained for audit, excluded from metrics
      │
      └── verified / manual_confirmed
                      │
                      ▼
Versioned metric snapshot + Neo4j evidence graph
```

## Product views

| View | Role |
|---|---|
| **Overview** | Immediate market map using the bundled demonstration profiles. |
| **Market Tables** | Capital, builder, demand, competition, and house-edge comparisons. |
| **Follow the Money** | Downstream-spending and shared-bottleneck view. |
| **Pain Graph** | Capital exposure, dependencies, vendor density, urgency, and budget surface. |
| **Market Graph** | Interactive market ontology and, after sync, the Neo4j relationship graph. |
| **Evidence Engine** | Source criteria, source-to-claim policy, claim review queue, exact-metric snapshots, and graph synchronization. |

The original visual profiles in `data/market_intelligence.json` remain **clearly labelled demonstration profiles**. The production calculation engine does not use them as observed data. It uses only verified or manually confirmed claims in `data/claims.json`.

## Tavily criteria registry

`data/search_criteria.json` is the editable collection control plane. It ships with 12 focused criteria spanning the full product model.

| Signal layer | Criteria |
|---|---|
| **Capital** | Funding rounds; investor and partner activity. |
| **Builders** | GitHub projects; accelerator cohorts; product and company launches. |
| **Demand** | Enterprise hiring; procurement/RFPs; customer adoption and case studies. |
| **Commercial proof** | Revenue and traction; company outcomes. |
| **Market structure** | Technology dependencies and pain points; vendor and competitor landscape. |

Each criterion defines its signal type, focused queries, Tavily topic, preferred domains, and expected claim types. Collection is manually initiated through the Evidence Engine or `POST /api/evidence/collect`; it does not silently invoke live web calls.

## Unified collector architecture

The application now uses **one evidence pipeline with complementary collectors**, not separate applications.

```text
Tavily web research ───────────────┐
GitHub REST builder metrics ───────┼──► Canonical evidence ─► Review-required claims ─► Neo4j + metrics + Idea Casino
SEC EDGAR filing/company facts ────┤
LLM structured interpretation ─────┘          ▲
                                                │
                              The LLM interprets sources; it never verifies or replaces them.
```

| Component | Role in Idea Casino | Required configuration |
|---|---|---|
| **Tavily** | Discovers unstructured funding, news, company, accelerator, demand, pain, and market-context sources. | `TAVILY_API_KEY` |
| **GitHub REST API** | Produces Builder Flow evidence from repository metadata, topics, stars, forks, creation/push dates, recent commit activity, and contributors. | `GITHUB_TOKEN` and `GITHUB_USER_AGENT` |
| **SEC EDGAR** | Produces Capital Flow and financial evidence from configured company submissions, Form D where exposed, and XBRL company facts. | `SEC_USER_AGENT` and `SEC_EDGAR_CIK_TARGETS`; no SEC API key |
| **LLM interpreter** | Converts source text into structured candidate entities, funding, investor, classification, demand, revenue, outcome, and dependency fields. | `LLM_ENABLED=true`, `LLM_API_KEY`, and optional `LLM_BASE_URL` |
| **Neo4j** | Retains the evidence-to-claim provenance graph and joins capital, builder, demand, and pain signals. | `NEO4J_*` |
| **CircleCI** | Runs the unified pipeline on an explicit scheduled or manual trigger. It is an orchestrator, not a source. | CircleCI context named `idea-casino-collection` |

GitHub Search permits up to 1,000 results per search query and uses its own search rate limit; repository statistic endpoints may return `202` while cached statistics are being prepared. The collector records response state rather than treating an unavailable statistic as zero.[3] [4] SEC’s JSON APIs provide submissions history and XBRL facts; SEC requires an identifying User-Agent and publishes a current maximum rate of 10 requests per second, which the collector respects with a configurable delay.[5] [6]

## Normalize → enrich → validate → admit

The collection pipeline is deliberately divided into four jobs with different trust boundaries:

```text
Tavily / GitHub / SEC EDGAR
           ↓
normalize.py     deterministic document standardization
           ↓
enrich.py        ontology-constrained LLM extraction, classification, and inference
           ↓
validate.py      deterministic source, taxonomy, plausibility, and duplicate checks
           ↓
Human review     verified source fact or accepted inference
           ↓
Neo4j            provenance-aware graph projection
```

| Stage | Does | Must not do |
|---|---|---|
| **Normalize** | Canonicalizes URLs, source dates, stage labels, money mentions, text fields, and raw provenance into one document contract. | Infer a company, market, buyer, dependency, or investment meaning. |
| **Enrich** | Uses a strict JSON schema and `ontology_taxonomy.json` to extract events and classify company → theme → subcategory → application → technology. | Invent taxonomy labels or pass a candidate as verified. |
| **Validate** | Checks literal company, amount, stage, investor, quote, URL, date, ontology membership, plausibility, and duplicate-event fingerprints. | Replace human judgement or silently correct a disputed extraction. |
| **Admit** | Sends only reviewed source facts and separately accepted inferences to Neo4j. | Treat a model inference as a fact or use it in production metric calculations. |

The controlled taxonomy has an explicit `OTHER` value. The enricher cannot invent a near-duplicate category such as “AI-security” when `AI Security` is the approved label. New candidate categories must be proposed outside the trusted graph and added to the taxonomy intentionally.

### Source facts versus model inferences

A source statement such as “Acme uses NVIDIA GPUs” becomes a candidate `USES` relationship with `evidence_type=SOURCE_FACT`, an exact literal quote, validation checks, and then human verification. A model conclusion such as “Acme likely needs simulation” becomes `LIKELY_NEEDS` with `evidence_type=MODEL_INFERENCE`, a separate provenance label, confidence, and rationale. It may be accepted for research exploration, but it cannot enter formula-backed market metrics as a verified source fact.

The review interface exposes **Validate**, **Verify**, and **Accept inference** as distinct actions. The graph stores `evidence_type`, `provenance`, `validation_status`, literal quote, rationale, and claim ID on projected relationships. This preserves the difference between a solid-edge source fact and a logically separate model hypothesis.

## LLM-orchestrated idea research

The homepage no longer needs to be treated as a fixed-profile lookup. **Research the idea** sends an LLM a constrained planning task: interpret the user’s concept, select only known markets and approved retrieval criteria, inspect local Neo4j/evidence/metric context, assess freshness and coverage, and then decide whether bounded external research is necessary.

| User mode | Retrieval behavior |
|---|---|
| **Use local evidence only** | The LLM uses the existing graph, evidence store, verified claims, and metric snapshots. It never invokes an external source. |
| **Local first · refresh if stale** | The default. It consults local context first, then invokes only bounded Tavily and market-relevant GitHub research if verified coverage is inadequate or evidence is older than the configured threshold. |
| **Refresh with live research** | It always consults local context, then runs bounded fresh Tavily and GitHub research before synthesis. |

The LLM is an **orchestrator and source-grounded explainer**, not the market-data authority. It never contributes its unstated background knowledge as Idea Casino evidence, does not create calculated metric values, and labels newly collected results as unverified until a reviewer confirms their claims. The returned answer includes the retrieval plan, graph-availability status, refresh decision, evidence coverage, uncertainty statements, and clickable source records.

Configure this path with `LLM_ENABLED=true`, `LLM_API_KEY`, `IDEA_QUERY_POLICY_FILE`, and the `IDEA_QUERY_*` limits in `.env`. Live refresh additionally requires the relevant Tavily and GitHub credentials. The policy is stored in `data/idea_query_policy.json`, where you can revise evidence-age thresholds, the minimum verified-claim requirement, and max fresh-query limits.

## Exact calculations

The canonical definitions live in `data/calculation_definitions.json`. Each result is stored as a `MetricSnapshot` with a formula version, time window, calculation time, raw input IDs, denominator safeguards, detailed factors, and an explicit status. If required approved evidence is missing, the status is `insufficient_evidence` rather than a manufactured value.

| Metric | Implemented formula |
|---|---|
| **Capital deployed** | `SUM(verified funding round amount)` in the current window. |
| **Capital velocity** | `(current capital − previous capital) / MAX(previous capital, $1M)`. |
| **Active builders** | Distinct verified projects, cohort companies, or launches in the current window. |
| **Capital per Builder** | `capital deployed / MAX(active builders, 1)`. |
| **Buyer demand** | Weighted verified demand events: jobs = 1, adoption = 3, procurement = 5, contract = 5; normalized within the comparison set. |
| **Revenue evidence** | `LOG1P(verified revenue + contract amounts)`, normalized within the comparison set. |
| **Builder density** | Active builders divided by the number of markets in its theme, normalized in the comparison set. |
| **Competitive intensity** | Distinct verified competitor, vendor, funding, and launch companies, normalized in the comparison set. |
| **Original multiplicative Opportunity Score** | `100 × (capital momentum × buyer demand × revenue evidence) / MAX(builder density × competitive intensity, 0.01)`. |
| **Picks & Shovels Score** | `100 × dependency × adoption × switching cost × monetization`. |
| **Pain Density** | `SUM(company funding × company-to-pain dependency weight)`. |
| **Pain Opportunity** | `100 × (pain density × customer growth × urgency × budget availability) / MAX(existing vendors × builder density, 0.01)`. |
| **Downstream Dollar** | `SUM(company funding × reviewed spend-allocation coefficient)` per technical dependency or pain category. |
| **House Edge** | `100 × NORMALIZE(0.45 × builder density + 0.35 × competition + 0.20 × (1 − buyer demand))`. |

The quotient-based scores clamp inputs to a documented factor range of `0.01–1.0` to protect denominators. They are then bounded to `0–100`; missing required components produce `insufficient_evidence`.

## Neo4j ontology

The original partner → firm → round → company → subcategory → theme graph is preserved. The expanded graph records both the assertion and its evidence.

```text
CollectionRun ──EXECUTED──> SearchCriterion
CollectionRun ──RETRIEVED──> Evidence ──SUPPORTS──> Claim

Claim ──ABOUT──> Company ──RAISED──> FundingRound
InvestorFirm ──INVESTED_IN──> FundingRound <──CHAMPIONED── Partner
Partner ──PARTNER_AT──> InvestorFirm
Company ──CLASSIFIED_AS──> Market ──PART_OF──> Theme

Builder ──BUILDS──> Project ──TARGETS──> Market
Company ──IN_COHORT──> AcceleratorCohort
Buyer ──EMITS──> DemandEvent ──DEMANDS──> Market
Company ──REPORTED──> RevenueEvent
Company ──HAD_OUTCOME──> Outcome
Company ──FILED──> SecFiling

Company ──USES──> Technology ──ENABLES──> Capability
Company ──LIKELY_NEEDS──> OntologyConcept   (MODEL_INFERENCE only)
Company ──USES / HAS_TECHNOLOGY──> OntologyConcept   (SOURCE_FACT / MODEL_CLASSIFICATION)
Company ──NEEDS──> PainPoint ──ADDRESSES──> Solution <──PROVIDES── Vendor
MetricSnapshot ──MEASURES──> Market
MetricSnapshot ──DERIVED_FROM──> Claim
```

`POST /api/evidence/graph/ingest` upserts collection runs, evidence, claims, and their domain projections. `POST /api/metrics/graph/ingest` upserts the versioned metric snapshots. Both operations retain claims and evidence in the graph so every metric can be traced back to source records.

## Run with Docker

```bash
cp .env.example .env
```

Set the required source and graph credentials in `.env`. Keep all secrets out of version control.

```dotenv
TAVILY_API_KEY=tvly-your-real-key
GITHUB_TOKEN=github_pat_read_only_token
GITHUB_USER_AGENT=IdeaCasino/1.0 contact@your-domain.example
SEC_USER_AGENT=YourCompany admin@your-domain.example
SEC_EDGAR_CIK_TARGETS=[{"cik":"0000320193","market_id":"mcp-security","company":"Example Company"}]
LLM_ENABLED=false
LLM_API_KEY=YOUR_LLM_API_KEY
NEO4J_PASSWORD=choose-a-strong-password
```

`GITHUB_TOKEN` should be read-only. Leave `LLM_ENABLED=false` until you have intentionally selected an OpenAI-compatible endpoint and accepted the model cost. The current default is `gpt-5-mini`, selected for structured extraction; every output still requires a human review.

Then start the containers:

```bash
docker compose up --build
```

Open [http://localhost:8000](http://localhost:8000). Neo4j Browser is available at [http://localhost:7474](http://localhost:7474).

## Collection and review workflow

First run a deliberately small criterion selection, such as one funding query, from the Evidence Engine. Review the resulting claims and enrich them only with information supported by the linked source. Use **Verify** for a complete extraction, **Reject** for an unsupported or incorrect claim, or **Enrich** to submit a source-supported JSON patch and mark it `manual_confirmed`.

Do not treat any claim with `review_required` as an input to a production score. After enough relevant verified claims exist in two comparable time windows, select **Recalculate metrics**. The engine records all metric statuses; this means a score may be absent until the necessary data exists.

## CircleCI automation

`.circleci/config.yml` deliberately validates every change but only runs data collection when the typed pipeline parameter `run_collection=true` is supplied. This prevents every code push from spending API quota or model budget. Configure the **`idea-casino-collection`** CircleCI context with `TAVILY_API_KEY`, `GITHUB_TOKEN`, `GITHUB_USER_AGENT`, `SEC_USER_AGENT`, `SEC_EDGAR_CIK_TARGETS`, `LLM_*` (only when enabled), and `NEO4J_*`; contexts inject these values at runtime rather than storing them in the repository.[7]

Create a daily schedule trigger for the protected `main` branch in CircleCI Project Settings, assign the `run_collection=true` pipeline parameter, and select an actor authorized to use the restricted context. CircleCI schedules use UTC and may have a small deterministic delay, so treat the configured `CIRCLECI_SCHEDULE_UTC=0 6 * * *` as the desired time rather than a precise execution guarantee.[8]

The invoked command is:

```bash
python scripts/run_pipeline.py --strict
```

It executes enabled Tavily, GitHub, SEC EDGAR, and optional LLM stages, calculates metrics, synchronizes Neo4j, stores run audit records, and returns a non-zero status if an enabled step fails. For a one-off local run, omit `--strict` if you want a complete report even when one source is temporarily unavailable.

## Key API routes

| Route | Description |
|---|---|
| `GET /api/evidence/criteria` | Returns the editable Tavily criteria registry and collection defaults. |
| `POST /api/evidence/collect` | Runs selected criteria. It creates normalized evidence documents; ontology interpretation is deferred. Example body: `{"criteria_ids":["funding_rounds"],"max_queries":1}`. |
| `POST /api/claims/validate` | Runs deterministic literal-evidence, taxonomy, plausibility, and duplicate checks on pending candidates. |
| `GET /api/idea-research/policy` | Returns the local-first freshness, coverage, and external-research policy. |
| `POST /api/idea-research` | Runs LLM-planned, source-grounded idea research. Example body: `{"idea":"MCP security testing for agents","mode":"local_first"}`. |
| `GET /api/evidence` | Lists canonical evidence, optionally by criterion or state. |
| `GET /api/direct-collectors/config` | Returns the GitHub and SEC EDGAR collection taxonomy. |
| `POST /api/direct-collectors/collect` | Runs `github`, `sec_edgar`, or `all` structured collectors. |
| `POST /api/evidence/llm-enrich` | Runs optional `gpt-5-mini` structured interpretation; resulting claims are always review-required. |
| `GET /api/claims` | Lists normalized claims, optionally by state or type. |
| `POST /api/claims/{id}/review` | Verifies, rejects, or manually confirms a claim with an optional source-supported payload patch. |
| `GET /api/collection-runs` | Returns collection run audit records and Tavily usage metadata. |
| `POST /api/evidence/graph/ingest` | Loads evidence and claims into Neo4j. |
| `GET /api/calculations/definitions` | Returns the full versioned formulas and requirements. |
| `POST /api/metrics/calculate` | Calculates exact metric snapshots from approved claims. |
| `GET /api/metrics` | Lists stored metric snapshots by market or status. |
| `GET /api/metrics/{market_id}/drilldown` | Returns metric snapshots, supporting claims, and definitions for one market. |
| `POST /api/metrics/graph/ingest` | Loads metric snapshots into Neo4j. |
| `GET /api/graph` | Returns the combined Neo4j graph as JSON. |

## Data files

| File | Purpose |
|---|---|
| `data/search_criteria.json` | Editable Tavily collection criteria. |
| `data/ontology_schema.json` | Node, relationship, claim-type, and evidence-state contract. |
| `data/calculation_definitions.json` | Exact calculation formulas, factor semantics, and data requirements. |
| `data/tavily_evidence_raw.json` | Append-only raw Tavily responses created by collections. |
| `data/direct_api_criteria.json` | GitHub search taxonomy and SEC EDGAR collector configuration. |
| `data/idea_query_policy.json` | Local-only, local-first, and live-refresh retrieval modes, freshness thresholds, source boundaries, and bounded research limits. |
| `data/ontology_taxonomy.json` | Controlled theme, subcategory, application, technology, dependency, buyer, event, stage, and relationship vocabulary used by enrichment and validation. |
| `data/direct_api_raw.json` | Append-only raw GitHub and SEC EDGAR records created by collections. |
| `data/evidence.json` | Canonical, deduplicated source evidence records. |
| `data/claims.json` | Normalized claims and review state. |
| `data/collection_runs.json` | Audited Tavily query, result, error, request, and usage metadata. |
| `data/metric_snapshots.json` | Versioned calculation output and supporting claim IDs. |
| `data/market_intelligence.json` | Demonstration-only visual seed profiles; not production metric input. |

## References

[1]: https://docs.tavily.com/documentation/api-reference/endpoint/search "Tavily Search API Reference"
[2]: https://docs.tavily.com/documentation/best-practices/best-practices-search "Tavily Search Best Practices"
[3]: https://docs.github.com/en/rest/search/search "GitHub REST Search API"
[4]: https://docs.github.com/en/rest/metrics/statistics "GitHub REST Repository Statistics API"
[5]: https://www.sec.gov/search-filings/edgar-application-programming-interfaces "SEC EDGAR Application Programming Interfaces"
[6]: https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data "SEC EDGAR Fair Access Guidance"
[7]: https://circleci.com/docs/guides/security/contexts/ "CircleCI Contexts"
[8]: https://circleci.com/docs/guides/orchestrate/schedule-triggers/ "CircleCI Schedule Triggers"


## Core Intelligence API, MCP, and Skill

Idea Casino now has a single **read-only Core Intelligence API** that is shared by the web interface, the MCP server, and the guided analysis skill. This avoids duplicating scoring logic or allowing an agent integration to bypass the evidence and provenance rules.

```text
Tavily + GitHub + SEC EDGAR
            ↓
Normalize → Enrich → Validate → Review
            ↓
Neo4j + reviewed evidence + metric snapshots
            ↓
Idea Casino Core Intelligence API
       ┌───────────────┬──────────────────┐
       ↓               ↓                  ↓
    Web UI       Read-only MCP       Guided Skill
```

### Core API

All `/api/core/*` routes are read-only and never collect data, call the LLM, enrich claims, validate claims, review claims, write Neo4j, or recalculate metrics. They preserve the same disclosure rules used by the Evidence Engine.

| Route | Purpose | Data boundary |
|---|---|---|
| `GET /api/core/catalog` | Service capability, count, data-tier, and boundary summary. | Metadata only. |
| `GET /api/core/funding` | Reviewed funding facts filtered by theme and days. | No demonstration-profile fallback. |
| `GET /api/core/investors/{company}` | Company, investor, partner, and round relationships. | Source facts by default; accepted inferences only when requested. |
| `GET /api/core/partners/{partner}` | Reviewed partner participation over a time window. | Source facts only. |
| `GET /api/core/compare-builder-capital` | Capital and builder comparison for a theme. | Each value labels `verified_metric_snapshot` or `demonstration_profile`. |
| `GET /api/core/underbuilt` | Profile-classified underbuilt markets. | Demonstration tier is explicit. |
| `GET /api/core/pain-graph` | Second-order pain opportunities for a theme. | Demonstration tier is explicit. |
| `POST /api/core/check-idea` | Category match, signals, pain points, investor context, metrics, and evidence. | Returns limitations and data tier. |
| `POST /api/core/explain-idea` | Stored Idea Odds explanation. | Profile-derived explanation is labelled demonstration data. |

Example:

```bash
curl -sS -X POST http://localhost:8000/api/core/check-idea \
  -H 'content-type: application/json' \
  -d '{"idea":"MCP runtime security","geography":"US","stage":"pre-seed"}'
```

### Read-only MCP server

The included MCP server exposes the Core API to compatible agent hosts. It uses the official Python MCP SDK and standard stdio transport; its typed tools are `service_catalog`, `check_idea`, `explain_idea_odds`, `search_funding`, `get_investor_graph`, `get_partner_activity`, `compare_builder_vs_capital`, `find_underbuilt_markets`, and `get_pain_graph`.[7]

It is intentionally **read-only**. It cannot run collectors, invoke external research, call an LLM, modify review states, write to Neo4j, or alter calculations. Each returned result retains a data tier and separates reviewed source facts from accepted model inferences.

Run the ordinary product with:

```bash
docker compose up --build
```

Run the stdio MCP service when a host launches it:

```bash
docker compose --profile mcp run --rm -T mcp
```

A host configuration template is available at `mcp/stdio_config.example.json`. Replace `/absolute/path/to/vcflow_app/docker-compose.yml` with the local absolute path. If using a non-containerized development setup, run `python -m app.mcp_server` from the repository root instead.

### Guided analysis skill

The distributable skill package lives at `idea-casino-analysis.skill` when delivered with this project. It instructs another assistant to begin with `service_catalog`, select only the relevant read-only MCP tools, preserve sources and claim provenance, distinguish facts from model inferences, label demonstration profiles, and report evidence gaps instead of guessing.

| Returned tier | Correct interpretation |
|---|---|
| `verified_source_facts` | Reviewed source-backed observation. |
| `accepted_model_inference` | Explicitly labelled hypothesis, not an observed fact. |
| `demonstration_profile` | Seeded UI/product-model signal, not a live measurement. |
| Empty response or `insufficient_evidence` | The evidence store cannot support the claim. |

## References

[7]: https://py.sdk.modelcontextprotocol.io/ "MCP Python SDK documentation"
