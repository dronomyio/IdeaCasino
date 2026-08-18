import os, re, json, hashlib
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlparse
import requests

MONEY_RE = re.compile(r"\$(\d+(?:\.\d+)?)\s*(billion|million|bn|b|m)\b", re.I)
STAGE_RE = re.compile(r"\b(pre[- ]?seed|seed|series\s+[a-h]|growth|venture|strategic)\b", re.I)
ROUND_VERBS = re.compile(r"\b(raises?|raised|funding|financing|round|backs?|invests?|investment)\b", re.I)

THEME_RULES = [
    ("AI Security", ["ai security","agent security","cybersecurity","identity","authorization","prompt injection","mcp security","runtime security"]),
    ("Agent Infrastructure", ["ai agent","agents","agentic","mcp","memory","orchestration","evaluation","evals"]),
    ("Developer Tools", ["developer tool","coding","code review","devops","testing","software development"]),
    ("Robotics / Physical AI", ["robotics","robot","humanoid","drone","autonomous","physical ai"]),
    ("AI Infrastructure", ["inference","gpu","compute","data center","datacenter","foundation model","llm infrastructure","ai infrastructure"]),
    ("Fintech Infrastructure", ["fintech","payments","stablecoin","trading","brokerage","financial infrastructure","defi"]),
    ("Healthcare AI", ["healthcare","clinical","medical","biotech","drug discovery"]),
    ("Data / Observability", ["observability","monitoring","telemetry","database","data platform","analytics"]),
]

SUBCATEGORY_RULES = [
    ("MCP Security", ["mcp security","model context protocol security"]),
    ("Agent Security", ["agent security","ai agent security","prompt injection"]),
    ("Identity / Auth", ["identity","authorization","authentication","non-human identity"]),
    ("AI Evaluation", ["evaluation","evals","benchmark"]),
    ("Agent Frameworks", ["agent framework","agentic framework","orchestration"]),
    ("Code Intelligence", ["code review","coding agent","developer tool"]),
    ("Humanoids", ["humanoid"]),
    ("Drones", ["drone","uav"]),
    ("Robotics", ["robotics","robot"]),
    ("Inference / Compute", ["inference","gpu","compute"]),
    ("Data Centers", ["data center","datacenter"]),
    ("Payments / Stablecoins", ["payments","stablecoin","usdc"]),
    ("Trading Infrastructure", ["trading","brokerage","market infrastructure"]),
    ("Observability", ["observability","monitoring","telemetry"]),
]


def _money_to_usd(text):
    m = MONEY_RE.search(text or "")
    if not m: return None
    n = float(m.group(1)); u = m.group(2).lower()
    mult = 1_000_000_000 if u in ("billion","bn","b") else 1_000_000
    return int(n*mult)


def _classify(text):
    t=(text or "").lower()
    theme="Other"; sub="Other"
    for name, keys in THEME_RULES:
        if any(k in t for k in keys): theme=name; break
    for name, keys in SUBCATEGORY_RULES:
        if any(k in t for k in keys): sub=name; break
    if sub == "Other": sub = theme
    return theme, sub


def _guess_company(title):
    if not title: return None
    # Common funding headline forms: "Acme raises $10M...", "$10M for Acme", "Acme lands..."
    m=re.match(r"^\s*([^|:–—-]{2,80}?)\s+(?:raises?|raised|lands?|secures?|closes?|gets?|bags?)\b", title, re.I)
    if m: return m.group(1).strip(' "\'')
    m=re.search(r"\bfor\s+([A-Z][A-Za-z0-9 .&+\-]{1,60})(?:[,;:]|$)", title)
    if m: return m.group(1).strip()
    return None


def _stage(text):
    m=STAGE_RE.search(text or "")
    if not m: return "Unknown"
    s=m.group(1).title().replace("Pre Seed","Pre-Seed")
    return s


def _date(result):
    for k in ("published_date","publishedDate","date"):
        v=result.get(k)
        if v:
            return str(v)[:10]
    return datetime.now(timezone.utc).date().isoformat()


def _domain(url):
    try: return urlparse(url).netloc.replace('www.','')
    except: return ""


class TavilyIngestor:
    def __init__(self):
        self.api_key=os.getenv("TAVILY_API_KEY","")
        self.endpoint=os.getenv("TAVILY_SEARCH_URL","https://api.tavily.com/search")
        self.data_dir=Path(os.getenv("DATA_DIR","/app/data"))
        self.sources_file=Path(os.getenv("SOURCES_FILE", str(self.data_dir/'sources.json')))
        self.raw_file=Path(os.getenv("TAVILY_RAW_FILE", str(self.data_dir/'tavily_raw.json')))
        self.candidates_file=Path(os.getenv("TAVILY_CANDIDATES_FILE", str(self.data_dir/'tavily_candidates.json')))
        self.max_results=int(os.getenv("TAVILY_MAX_RESULTS","10"))
        self.search_depth=os.getenv("TAVILY_SEARCH_DEPTH","basic")
        self.time_range=os.getenv("TAVILY_TIME_RANGE","month")
        self.include_raw=os.getenv("TAVILY_INCLUDE_RAW_CONTENT","true").lower()=="true"

    def _sources(self):
        return json.loads(self.sources_file.read_text())

    def _search(self, query, domains=None, topic="news"):
        if not self.api_key: raise RuntimeError("TAVILY_API_KEY is not configured")
        payload={
            "api_key": self.api_key,
            "query": query,
            "topic": topic,
            "search_depth": self.search_depth,
            "max_results": self.max_results,
            "include_answer": False,
            "include_raw_content": self.include_raw,
            "time_range": self.time_range,
        }
        if domains: payload["include_domains"]=domains
        r=requests.post(self.endpoint,json=payload,timeout=90)
        r.raise_for_status()
        return r.json()

    def collect(self):
        cfg=self._sources()
        searches=[]
        for src in cfg.get("sources", cfg.get("funding_sources", [])):
            if not src.get("enabled", True): continue
            domain=src.get("domain") or _domain(src.get("url",""))
            queries=src.get("queries") or ["startup funding raises seed Series A Series B venture capital"]
            for q in queries:
                try:
                    res=self._search(q,[domain] if domain else None,src.get("topic","news"))
                    searches.append({"source":src.get("name"),"domain":domain,"query":q,"response":res})
                except Exception as e:
                    searches.append({"source":src.get("name"),"domain":domain,"query":q,"error":str(e),"response":{"results":[]}})
        self.raw_file.write_text(json.dumps({"collected_at":datetime.now(timezone.utc).isoformat(),"searches":searches},indent=2))
        candidates=self.normalize(searches)
        self.candidates_file.write_text(json.dumps(candidates,indent=2))
        return {"searches":len(searches),"candidates":len(candidates),"raw_file":str(self.raw_file),"candidates_file":str(self.candidates_file)}

    def normalize(self, searches):
        out=[]; seen=set()
        for s in searches:
            for r in s.get("response",{}).get("results",[]) or []:
                url=r.get("url","")
                if not url or url in seen: continue
                seen.add(url)
                title=r.get("title") or ""
                content=r.get("raw_content") or r.get("content") or ""
                text=f"{title}\n{content[:12000]}"
                if not ROUND_VERBS.search(text): continue
                amount=_money_to_usd(text)
                company=_guess_company(title)
                theme,subcategory=_classify(text)
                stage=_stage(text)
                cid=hashlib.sha1(url.encode()).hexdigest()[:16]
                confidence=0
                if company: confidence+=35
                if amount: confidence+=30
                if stage!="Unknown": confidence+=15
                if theme!="Other": confidence+=10
                if ROUND_VERBS.search(title): confidence+=10
                out.append({
                    "id":cid,"company":company,"date":_date(r),"round":stage if stage!="Unknown" else "Funding Round",
                    "amount_usd":amount,"stage":stage,"theme":theme,"subcategory":subcategory,
                    "investors":[],
                    "news":[{"title":title,"url":url,"source":s.get("source") or _domain(url),"published_date":_date(r)}],
                    "source_url":url,"source_domain":_domain(url),"source_name":s.get("source"),
                    "confidence":confidence,"review_required": confidence < 70 or not company or amount is None,
                    "snippet":(r.get("content") or "")[:1000]
                })
        return sorted(out,key=lambda x:(x.get("date") or "",x.get("confidence",0)),reverse=True)
