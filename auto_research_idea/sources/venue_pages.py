"""Official venue accepted-paper pages — the authoritative, complete listings.

Conferences publish a single page listing every accepted paper, e.g.
`https://cvpr.thecvf.com/virtual/2026/papers.html`. These are the most
authoritative source for "what got into CVPR 2026", so we mine them directly:
fetch the page (once, cached), extract every paper title, filter to the search
topic, and tag each paper with the venue+year derived from the URL.

The thecvf virtual sites (CVPR / ICCV / WACV) share one HTML layout, so those
URLs are synthesized automatically for recent years; any other listing pages can
be added via `venue_pages.urls` in config.yaml.

Like every source, this never raises — it returns [] on any network/parse error.
"""

from __future__ import annotations

import html
import logging
import re
import time
from datetime import datetime

from ..models import Paper
from ._http import get_with_retry
from .base import PaperSource
from .github_awesome import _clean_title, _is_paper_url, _keywords

logger = logging.getLogger(__name__)

CURRENT_YEAR = datetime.now().year

# thecvf virtual-site subdomains and how often they run, so we only synthesize
# URLs that plausibly exist (avoids slow 404 retries on, e.g., even-year ICCV).
_THECVF_ANNUAL = {"cvpr": "CVPR", "wacv": "WACV"}
_THECVF_ODD_YEARS = {"iccv": "ICCV"}  # ICCV is held in odd years only

# Paper-detail anchors on a listing page: /virtual/<year>/poster/<id>, plus
# oral/paper/forum variants used by some sites.
_PAPER_ANCHOR_RE = re.compile(
    r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL
)
_PAPER_PATH_RE = re.compile(r"/(?:virtual/\d{4}/)?(?:poster|oral|paper|forum)/", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_YEAR_IN_URL_RE = re.compile(r"/(20[12]\d)(?:/|\b)")

# A paper's detail page carries its abstract — either in a citation_abstract meta
# tag or in an `abstract`-classed block (the thecvf virtual layout).
_META_ABSTRACT_RE = re.compile(
    r'<meta[^>]+name=["\']citation_abstract["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE
)
_ABSTRACT_BLOCK_RE = re.compile(
    r"<(\w+)[^>]*class=['\"][^'\"]*abstract[^'\"]*['\"][^>]*>(.*?)</\1>",
    re.IGNORECASE | re.DOTALL,
)
_LEADING_ABSTRACT_RE = re.compile(r"^\s*abstract\s*[:.]?\s*", re.IGNORECASE)

# A detail page usually links the PDF — via a citation_pdf_url meta tag, an arXiv
# link, or an openaccess/.pdf href. We store it so the paper can be re-fetched.
_PDF_META_RE = re.compile(
    r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE
)
_PDF_HREF_RES = [
    re.compile(r'href=["\'](https?://arxiv\.org/(?:abs|pdf)/\d{4}\.\d{4,5}[^"\']*)["\']', re.IGNORECASE),
    re.compile(r'href=["\'](https?://[^"\']*openaccess\.thecvf\.com/[^"\']+\.pdf)["\']', re.IGNORECASE),
    re.compile(r'href=["\'](https?://[^"\']+\.pdf)["\']', re.IGNORECASE),
]


def _strip_tags(markup: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", markup))).strip()


def _norm_key(title: str) -> str:
    """Normalized title key for matching across sites (entity-decoded, alnum)."""
    return "".join(c for c in html.unescape(title or "").lower() if c.isalnum())


def _extract_pdf_url(markup: str) -> str:
    m = _PDF_META_RE.search(markup)
    if m:
        return m.group(1).strip()
    for rx in _PDF_HREF_RES:
        m = rx.search(markup)
        if m:
            return m.group(1).strip()
    return ""


# The CVF openaccess site hosts every paper's PDF on a single per-conference index
# (e.g. https://openaccess.thecvf.com/CVPR2026?day=all) — the authoritative PDF
# source for thecvf venues, even when the virtual poster page doesn't link it.
_OA_BASE = "https://openaccess.thecvf.com"
_OA_PTITLE_RE = re.compile(
    r'class="ptitle">(?:<br>)?<a href="(/content/[^"]+?/html/[^"]+?\.html)">(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)


def _openaccess_url(venue_str: str) -> str:
    """'CVPR 2026' -> the openaccess all-papers index URL, or '' if not a CVF venue."""
    code = (venue_str or "").replace(" ", "")
    if not re.match(r"(CVPR|ICCV|ECCV|WACV)\d{4}$", code, re.IGNORECASE):
        return ""
    return f"{_OA_BASE}/{code}?day=all"


def _parse_openaccess_index(markup: str) -> dict:
    """Map normalized title -> direct PDF url from a CVF openaccess index page."""
    out = {}
    for m in _OA_PTITLE_RE.finditer(markup):
        href = m.group(1)
        key = _norm_key(_strip_tags(m.group(2)))
        if not key:
            continue
        pdf = _OA_BASE + href.replace("/html/", "/papers/")[:-5] + ".pdf"
        out[key] = pdf
    return out


def _extract_abstract(markup: str) -> str:
    """Pull the abstract text out of a paper detail page."""
    m = _META_ABSTRACT_RE.search(markup)
    if m and len(m.group(1)) > 60:
        return _strip_tags(m.group(1))
    best = ""
    for m in _ABSTRACT_BLOCK_RE.finditer(markup):
        text = _strip_tags(m.group(2))
        if len(text) > len(best):
            best = text
    return _LEADING_ABSTRACT_RE.sub("", best)


def _origin(url: str) -> str:
    m = re.match(r"(https?://[^/]+)", url)
    return m.group(1) if m else ""


def _venue_year_from_url(url: str):
    """Parse (venue_display, year) from a listing-page URL."""
    sub = ""
    m = re.match(r"https?://([^./]+)\.", url)
    if m:
        sub = m.group(1).lower()
    venue = _THECVF_ANNUAL.get(sub) or _THECVF_ODD_YEARS.get(sub)
    ym = _YEAR_IN_URL_RE.search(url) or re.search(r"20[12]\d", url)
    year = int(ym.group(1) if ym.lastindex else ym.group(0)) if ym else None
    if venue and year:
        return f"{venue} {year}", year
    return (venue or None), year


class VenuePagesSource(PaperSource):
    name = "venue_pages"

    def __init__(self, extra_urls=None, recent_years: int = 2, contact_email: str = "",
                 registry=None):
        self._extra_urls = list(extra_urls or [])
        self._registry = list(registry or [])
        self._recent_years = [CURRENT_YEAR - i for i in range(max(1, recent_years))]
        self._headers = {
            "User-Agent": f"auto-research-idea ({contact_email or 'research'})",
            "Accept": "text/html",
        }
        # Cache the parsed (title, url) candidates per page so repeated per-query
        # calls re-filter in memory instead of refetching ~700KB pages.
        self._page_cache: dict = {}
        # Cache per-paper detail (abstract + pdf_url) across queries.
        self._abstract_cache: dict = {}
        # Cache the openaccess title->pdf index per venue (e.g. "CVPR 2026").
        self._pdf_index_cache: dict = {}
        self._urls = None

    def _pdf_index(self, venue_str: str) -> dict:
        """Title->PDF map from the CVF openaccess index for a venue (cached).

        The index page is large, so allow a generous timeout; a non-CVF venue
        (or transient failure) yields {} and is NOT cached, so it can be retried.
        """
        cached = self._pdf_index_cache.get(venue_str)
        if cached:
            return cached
        url = _openaccess_url(venue_str)
        if not url:
            return {}
        mapping: dict = {}
        # NOTE: openaccess.thecvf.com returns 406 if sent an `Accept: text/html`
        # header, so fetch the (large) index with only a User-Agent.
        oa_headers = {"User-Agent": self._headers.get("User-Agent", "auto-research-idea")}
        resp = get_with_retry(url, params={}, headers=oa_headers, timeout=60, max_attempts=3)
        if resp is not None:
            mapping = _parse_openaccess_index(resp.text)
        if mapping:
            self._pdf_index_cache[venue_str] = mapping
        return mapping

    def _listing_urls(self) -> list:
        if self._urls is not None:
            return self._urls
        urls = list(self._extra_urls)
        # Config-driven registry: each entry has a `listing` template with {year}
        # and an optional `cadence` (annual|odd|even). This is the reusable record
        # of where each venue's official paper list lives.
        for entry in self._registry:
            tmpl = (entry or {}).get("listing", "")
            if not tmpl or "{year}" not in tmpl:
                continue
            cadence = (entry.get("cadence") or "annual").lower()
            for year in self._recent_years:
                if cadence == "odd" and year % 2 == 0:
                    continue
                if cadence == "even" and year % 2 == 1:
                    continue
                urls.append(tmpl.format(year=year))
        # Built-in default: the thecvf virtual sites (used when no registry given).
        if not self._registry:
            for year in self._recent_years:
                for sub in _THECVF_ANNUAL:
                    urls.append(f"https://{sub}.thecvf.com/virtual/{year}/papers.html")
                if year % 2 == 1:
                    for sub in _THECVF_ODD_YEARS:
                        urls.append(f"https://{sub}.thecvf.com/virtual/{year}/papers.html")
        # Dedup, preserve order.
        seen, out = set(), []
        for u in urls:
            if u and u not in seen:
                seen.add(u)
                out.append(u)
        self._urls = out
        return out

    def _candidates(self, url: str) -> list:
        """Return cached (title, paper_url) pairs parsed from one listing page."""
        if url in self._page_cache:
            return self._page_cache[url]
        # max_attempts=2: a missing page (e.g. a year not yet posted) shouldn't
        # cost the full backoff ladder.
        resp = get_with_retry(url, params={}, headers=self._headers, max_attempts=2)
        cands: list = []
        if resp is not None:
            origin = _origin(url)
            seen_titles: set = set()
            for m in _PAPER_ANCHOR_RE.finditer(resp.text):
                href, inner = m.group(1).strip(), m.group(2)
                if not (_PAPER_PATH_RE.search(href) or _is_paper_url(href)):
                    continue
                title = _clean_title(_TAG_RE.sub("", inner))
                if len(title) < 8 or " " not in title:
                    continue
                key = "".join(c for c in title.lower() if c.isalnum())
                if not key or key in seen_titles:
                    continue
                seen_titles.add(key)
                paper_url = href if href.startswith("http") else origin + href
                cands.append((title, paper_url))
        self._page_cache[url] = cands
        return cands

    def _detail_for(self, paper_url: str) -> dict:
        """Fetch a paper's detail page and extract {abstract, pdf_url} (cached).

        A short delay between detail fetches keeps a burst of them from tripping
        the venue site's rate limiter (which silently returns empty abstracts).
        Empty results are NOT cached, so a transient failure can be retried.
        """
        cached = self._abstract_cache.get(paper_url)
        if cached and cached.get("abstract"):
            return cached
        detail = {"abstract": "", "pdf_url": ""}
        resp = get_with_retry(paper_url, params={}, headers=self._headers, max_attempts=3)
        if resp is not None:
            detail["abstract"] = _extract_abstract(resp.text)
            detail["pdf_url"] = _extract_pdf_url(resp.text)
        if detail["abstract"]:
            self._abstract_cache[paper_url] = detail
        time.sleep(0.34)  # be polite to the venue site
        return detail

    def search(self, query: str, limit: int) -> list[Paper]:
        kw_set = set(_keywords(query))
        need = 1 if len(kw_set) <= 1 else 2
        papers: list[Paper] = []
        seen: set = set()
        for url in self._listing_urls():
            if len(papers) >= limit:
                break
            venue, year = _venue_year_from_url(url)
            for title, paper_url in self._candidates(url):
                if len(papers) >= limit:
                    break
                if kw_set and sum(1 for k in kw_set if k in title.lower()) < need:
                    continue
                key = _norm_key(title)
                if key in seen:
                    continue
                seen.add(key)
                detail = self._detail_for(paper_url)
                # Resolve a PDF: prefer one linked on the detail page, else the
                # authoritative CVF openaccess index (covers papers whose virtual
                # poster page links no PDF, e.g. all of CVPR 2026).
                pdf_url = detail["pdf_url"] or self._pdf_index(venue or "").get(key, "")
                papers.append(
                    Paper(
                        source=self.name,
                        source_id="venue:" + re.sub(r"^https?://", "", paper_url)[:80],
                        title=title,
                        abstract=detail["abstract"],
                        url=paper_url,
                        landing_url=paper_url,
                        pdf_url=pdf_url,
                        venue=venue or "",
                        year=year,
                    )
                )
        return papers[:limit]
