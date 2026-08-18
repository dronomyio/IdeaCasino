import json, os
from pathlib import Path

class FundingStore:
    def load(self): raise NotImplementedError

class FileFundingStore(FundingStore):
    def __init__(self, path: str): self.path = Path(path)
    def load(self):
        if not self.path.exists(): return []
        data=json.loads(self.path.read_text())
        return data if isinstance(data,list) else data.get("records",[])

class CandidateFundingStore(FileFundingStore):
    def load(self):
        rows=super().load()
        # Only auto-ingest sufficiently structured candidates. Review-required rows remain visible through /api/candidates.
        return [r for r in rows if not r.get("review_required") and r.get("company") and r.get("amount_usd") is not None]

def get_store(mode=None):
    mode=mode or os.getenv("STORE_MODE","file")
    if mode == "tavily_candidates":
        return CandidateFundingStore(os.getenv("TAVILY_CANDIDATES_FILE","/app/data/tavily_candidates.json"))
    return FileFundingStore(os.getenv("DATA_FILE", "/app/data/funding.json"))
