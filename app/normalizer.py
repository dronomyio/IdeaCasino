"""Deterministic source normalization for the Idea Casino evidence pipeline.

This stage does not infer business meaning. It standardizes incoming sources, preserves
raw provenance, canonicalizes URLs, and records literal source mentions that later
validation can compare against LLM enrichment output.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

MONEY_RE = re.compile(r"\$(\d+(?:\.\d+)?)\s*(trillion|billion|million|thousand|tn|bn|b|m|k)\b", re.I)
STAGE_RE = re.compile(r"\b(pre[- ]?seed|seed|series[- ]?[a-h]|growth|venture|strategic)\b", re.I)
DATE_RE = re.compile(r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{1,2},?\s+\d{4}\b", re.I)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_url(url: str) -> str:
    try:
        parts = urlsplit((url or "").strip())
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", ""))
    except Exception:
        return (url or "").strip()


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def standard_stage(value: str | None) -> str | None:
    if not value:
        return None
    normalized = re.sub(r"[-\s]+", " ", value.strip().lower())
    if normalized.startswith("series "):
        return "Series " + normalized.split()[-1].upper()
    return {"pre seed": "Pre-Seed", "seed": "Seed", "growth": "Growth", "venture": "Venture", "strategic": "Strategic"}.get(normalized)


def money_mentions(text: str) -> list[dict[str, Any]]:
    multipliers = {"trillion": 1_000_000_000_000, "tn": 1_000_000_000_000, "billion": 1_000_000_000, "bn": 1_000_000_000, "b": 1_000_000_000, "million": 1_000_000, "m": 1_000_000, "thousand": 1_000, "k": 1_000}
    return [{"text": match.group(0), "amount_usd": int(float(match.group(1)) * multipliers[match.group(2).lower()]), "start": match.start(), "end": match.end()} for match in MONEY_RE.finditer(text or "")]


def stage_mentions(text: str) -> list[dict[str, Any]]:
    return [{"text": match.group(0), "stage": standard_stage(match.group(0)), "start": match.start(), "end": match.end()} for match in STAGE_RE.finditer(text or "")]


def normalize_date(value: Any) -> str | None:
    if not value:
        return None
    raw = str(value).strip()
    for candidate in (raw, raw[:10]):
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
            return parsed.date().isoformat()
        except ValueError:
            pass
    for match in DATE_RE.finditer(raw):
        cleaned = match.group(0).replace(".", "")
        for fmt in ("%b %d, %Y", "%B %d, %Y"):
            try:
                return datetime.strptime(cleaned, fmt).date().isoformat()
            except ValueError:
                continue
    return None


def normalize_document(source: dict[str, Any], *, source_type: str, criterion_id: str | None = None, run_id: str | None = None) -> dict[str, Any]:
    """Create the common normalized document contract without ontology inference."""
    raw_text = str(source.get("raw_content") or source.get("content") or source.get("text") or source.get("description") or "")
    title = str(source.get("title") or source.get("name") or "")
    combined = f"{title}\n{raw_text}".strip()
    url = canonical_url(str(source.get("url") or source.get("html_url") or ""))
    return {
        "source": str(source.get("source") or source_type),
        "source_type": source_type,
        "url": url,
        "canonical_url": url,
        "source_domain": urlsplit(url).netloc.lower(),
        "external_id": str(source.get("id") or source.get("external_id") or url),
        "published_date": normalize_date(source.get("published_date") or source.get("publishedDate") or source.get("date") or source.get("filingDate")),
        "title": title,
        "text": raw_text,
        "content_excerpt": raw_text[:4000],
        "retrieved_at": utc_now(),
        "content_hash": content_hash(combined),
        "criterion_id": criterion_id,
        "run_id": run_id,
        "literal_mentions": {
            "amounts_usd": money_mentions(combined),
            "stages": stage_mentions(combined),
            "dates": [normalize_date(match.group(0)) for match in DATE_RE.finditer(combined) if normalize_date(match.group(0))],
        },
        "normalization_version": "normalizer_v1",
        "state": "normalized",
    }
