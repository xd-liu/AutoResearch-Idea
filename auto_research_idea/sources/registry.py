"""Build sources from config and fan out searches across them."""

from __future__ import annotations

import logging
import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from ..models import Paper
from .arxiv import ArxivSource
from .base import PaperSource
from .github_awesome import GitHubAwesomeSource
from .openalex import OpenAlexSource
from .pdf_extract import fetch_sections, pdf_url_for
from .semantic_scholar import SemanticScholarSource
from .venue_pages import VenuePagesSource

logger = logging.getLogger(__name__)

CURRENT_YEAR = datetime.now().year

# Top-tier venues for ML / CV / NLP / robotics. Abbreviations are matched as
# whole words (so "acl" doesn't fire on "miracle"); the long conference names are
# matched as substrings since OpenAlex often stores the spelled-out name.
_TOP_VENUE_ABBREVS = [
    "cvpr", "iccv", "eccv", "wacv",
    "neurips", "nips", "icml", "iclr",
    "aaai", "ijcai",
    "acl", "emnlp", "naacl", "coling",
    "iros", "icra", "corl", "rss",
    "kdd", "siggraph",
    "tpami", "ijcv",
]
_TOP_VENUE_PHRASES = [
    "computer vision and pattern recognition",
    "international conference on computer vision",
    "european conference on computer vision",
    "neural information processing systems",
    "international conference on machine learning",
    "international conference on learning representations",
    "association for computational linguistics",
    "empirical methods in natural language processing",
    "intelligent robots and systems",
    "robotics and automation",
    "conference on robot learning",
    "robotics: science and systems",
    "knowledge discovery and data mining",
    "pattern analysis and machine intelligence",
]
_ABBREV_RE = re.compile(r"\b(" + "|".join(_TOP_VENUE_ABBREVS) + r")\b")

# Composite-score weights. Priority (per the spec): relevance > recency >
# top-venue > citations — citations contribute but never dominate.
_W_RELEVANCE = 1.0
_W_RECENCY = 0.8
_W_VENUE = 0.6
_W_CITATION = 0.25


def _is_top_venue(venue: str) -> bool:
    v = (venue or "").lower()
    if not v:
        return False
    if _ABBREV_RE.search(v):
        return True
    return any(phrase in v for phrase in _TOP_VENUE_PHRASES)


def _recency_score(year) -> float:
    """1.0 for the current year, decaying 0.25/year, floored at 0.

    With min_year=2024 this means 2026->1.0, 2025->0.75, 2024->0.5 — strongly
    favoring the newest work in a fast-moving area.
    """
    if not year:
        return 0.0
    return max(0.0, 1.0 - 0.25 * (CURRENT_YEAR - year))


def _citation_score(count) -> float:
    """Log-normalized citation signal in [0, 1] (~1000 cites saturates to 1)."""
    if not count or count <= 0:
        return 0.0
    return min(1.0, math.log1p(count) / math.log1p(1000))


def build_sources(cfg) -> list[PaperSource]:
    """Instantiate the sources named in config."""
    built: list[PaperSource] = []
    for name in cfg.sources:
        if name == "arxiv":
            built.append(ArxivSource(contact_email=cfg.contact_email))
        elif name == "semantic_scholar":
            built.append(SemanticScholarSource(api_key=cfg.semantic_scholar_api_key))
        elif name == "openalex":
            built.append(OpenAlexSource(contact_email=cfg.contact_email))
        elif name == "github":
            built.append(GitHubAwesomeSource(token=cfg.github_token))
        elif name == "venue_pages":
            vp = cfg.venue_pages or {}
            built.append(VenuePagesSource(
                extra_urls=vp.get("urls", []),
                recent_years=vp.get("thecvf_years", 2),
                contact_email=cfg.contact_email,
            ))
        else:
            logger.warning("Unknown source %r in config; skipping.", name)
    return built


def _combine(a: Paper, b: Paper) -> Paper:
    """Merge two records of the same paper, keeping the richest value per field.

    Picking one whole record (by abstract length) and discarding the other loses
    fields that only one source carries — e.g. arXiv has the long abstract but no
    citation count/venue, while Semantic Scholar/OpenAlex have those. Ranking and
    the min_year filter run on these fields, so merge them field-by-field.
    """
    base, other = (a, b) if len(a.abstract) >= len(b.abstract) else (b, a)
    base.abstract = base.abstract or other.abstract
    base.year = base.year or other.year
    base.venue = base.venue or other.venue
    base.url = base.url or other.url
    if not base.authors:
        base.authors = other.authors
    cites = [c for c in (a.citation_count, b.citation_count) if c is not None]
    base.citation_count = max(cites) if cites else None
    return base


def _merge(papers: list[Paper]) -> list[Paper]:
    """Collapse duplicates (same title across sources), combining their fields."""
    by_key: dict[str, Paper] = {}
    for p in papers:
        if not p.title:
            continue
        key = p.dedup_key()
        existing = by_key.get(key)
        by_key[key] = p if existing is None else _combine(existing, p)
    return list(by_key.values())


def _title_tokens(t: str) -> set:
    return set(re.findall(r"[a-z0-9]+", t.lower()))


def _norm_title(t: str) -> str:
    return "".join(c for c in t.lower() if c.isalnum())


def _title_match(a: str, b: str) -> bool:
    """True if two titles plausibly refer to the same paper (tolerates subtitle /
    punctuation differences between a venue page and the indexed record)."""
    if not a or not b:
        return False
    na, nb = _norm_title(a), _norm_title(b)
    if na == nb:
        return True
    if len(na) >= 20 and (na in nb or nb in na):
        return True
    ta, tb = _title_tokens(a), _title_tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= 0.8


def _enrich_abstracts(papers: list[Paper], contact_email: str = "", max_lookups: int = 60) -> None:
    """Fill in abstracts for papers that have a title but no abstract (e.g. from
    venue pages / GitHub lists) by looking the title up in OpenAlex, then arXiv.

    Mutates papers in place. Bounded by max_lookups and never raises so a flaky
    lookup can't sink the run.
    """
    need = [p for p in papers if not p.abstract and p.title][:max_lookups]
    if not need:
        return
    oa = OpenAlexSource(contact_email=contact_email)
    ax = ArxivSource(contact_email=contact_email)
    for p in need:
        match = None
        try:
            for cand in oa.lookup_title(p.title):
                if cand.abstract and _title_match(p.title, cand.title):
                    match = cand
                    break
            if match is None:
                for cand in ax.lookup_title(p.title):
                    if cand.abstract and _title_match(p.title, cand.title):
                        match = cand
                        break
        except Exception as e:  # defensive: enrichment must never crash the run
            logger.warning("Abstract enrichment failed for %r: %s", p.title[:60], e)
            continue
        if match is None:
            continue
        p.abstract = match.abstract
        p.year = p.year or match.year
        p.venue = p.venue or match.venue
        if p.citation_count is None:
            p.citation_count = match.citation_count
        if not p.url:
            p.url = match.url


def _enrich_pdf_sections(papers: list[Paper], max_pdfs: int = 60) -> None:
    """For papers with a reachable PDF, fill in intro/conclusion (best effort).

    Bounded by max_pdfs; mutates in place; never raises (each download is guarded
    by the soft-failing HTTP + parse helpers)."""
    count = 0
    for p in papers:
        if count >= max_pdfs:
            break
        if p.intro or p.conclusion:
            continue
        url = pdf_url_for(p)
        if not url:
            continue
        count += 1
        sections = fetch_sections(url)
        if sections.get("intro"):
            p.intro = sections["intro"]
        if sections.get("conclusion"):
            p.conclusion = sections["conclusion"]


def _search_source(src: PaperSource, queries: list[str], per_query_limit: int) -> list[tuple]:
    """Run all queries against one source sequentially (gentle on its rate limit).

    Returns (paper, reciprocal_rank) pairs: each source returns results in
    relevance order per query, so 1/(position+1) is a within-query relevance
    signal. These are summed across queries/sources to score relevance.
    """
    out: list[tuple] = []
    for q in queries:
        try:
            results = src.search(q, per_query_limit)
        except Exception as e:  # defensive: a source should not crash the run
            logger.warning("Source %s failed on %r: %s", src.name, q, e)
            continue
        for pos, p in enumerate(results):
            out.append((p, 1.0 / (pos + 1)))
    return out


def search_all(
    sources: list[PaperSource],
    queries: list[str],
    *,
    per_query_limit: int,
    max_papers: int,
    min_year: int = 0,
    enrich_abstracts: bool = True,
    parse_pdf: bool = True,
    contact_email: str = "",
) -> list[Paper]:
    """Fan out across sources in parallel; within each source, query serially.

    This keeps concurrency to one in-flight request per host, which the paper
    APIs (especially arXiv and unauthenticated Semantic Scholar) tolerate far
    better than a burst of simultaneous requests.
    """
    collected: list[tuple] = []  # (paper, reciprocal_rank)
    with ThreadPoolExecutor(max_workers=max(1, len(sources))) as pool:
        futures = {
            pool.submit(_search_source, src, queries, per_query_limit): src.name
            for src in sources
        }
        for fut in as_completed(futures):
            collected.extend(fut.result())

    # Aggregate relevance per paper (sum of reciprocal ranks across queries/sources),
    # keyed by the same normalized title used to dedup.
    relevance_raw: dict[str, float] = {}
    for p, rr in collected:
        if not p.title:
            continue
        k = p.dedup_key()
        relevance_raw[k] = relevance_raw.get(k, 0.0) + rr

    merged = _merge([p for p, _ in collected])
    if min_year:
        merged = [p for p in merged if (p.year or 0) >= min_year]

    max_rel = max(relevance_raw.values(), default=1.0) or 1.0

    def _score(p: Paper) -> float:
        relevance = relevance_raw.get(p.dedup_key(), 0.0) / max_rel
        return (
            _W_RELEVANCE * relevance
            + _W_RECENCY * _recency_score(p.year)
            + _W_VENUE * (1.0 if _is_top_venue(p.venue) else 0.0)
            + _W_CITATION * _citation_score(p.citation_count)
        )

    merged.sort(key=_score, reverse=True)
    top = merged[:max_papers]
    if enrich_abstracts:
        # Only enrich the kept set, so abstract-less venue/GitHub picks become
        # digestible without an unbounded number of lookups.
        _enrich_abstracts(top, contact_email=contact_email)
    if parse_pdf:
        # Add intro/conclusion from each paper's PDF where one is reachable.
        _enrich_pdf_sections(top)
    return top
