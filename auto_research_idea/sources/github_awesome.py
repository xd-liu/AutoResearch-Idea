"""GitHub curated-list source — pre-organized paper lists.

Two kinds of community-maintained repos link hundreds of organized papers:

  1. `awesome-<topic>` lists — scoped to a research topic; we extract every paper.
  2. curated *venue* lists like `top-cvpr-2026-papers` / `iccv-2025-papers` —
     organized by conference + year, not by topic. These are gold for a
     fast-moving area (recent + top-tier venue), so we discover them too, tag
     each paper with the venue+year parsed from the repo name (which feeds the
     recency / top-venue ranking in `registry.py`), and filter their READMEs by
     the topic keywords so we only keep relevant papers.

Extracted arXiv papers merge (by title) with the arXiv/OpenAlex/S2 results, so
curated picks gain full metadata.

A GITHUB_TOKEN (env) is strongly recommended — unauthenticated GitHub API limits
(60 req/hr, 10 searches/min) are easily hit by the multi-search discovery below.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from ..models import Paper
from ._http import get_with_retry
from .base import PaperSource

logger = logging.getLogger(__name__)

_REPO_SEARCH = "https://api.github.com/search/repositories"

CURRENT_YEAR = datetime.now().year

# Hosts whose links are (almost) always papers, not random web pages.
_PAPER_HOSTS = (
    "arxiv.org", "openreview.net", "doi.org", "aclanthology.org",
    "dl.acm.org", "ieeexplore.ieee.org", "papers.nips.cc", "proceedings.",
    "jmlr.org", "openaccess.thecvf.com", "semanticscholar.org", "biorxiv.org",
)

_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})")

# HTML anchor cards used by many curated venue lists (e.g. SkalskiP/top-*-papers):
# `<a href="https://arxiv.org/abs/..." title="Real Paper Title">`. Attribute order
# varies, so href and title are matched independently within the tag.
_HTML_ANCHOR_RE = re.compile(r"<a\b([^>]*)>", re.IGNORECASE)
_ATTR_HREF_RE = re.compile(r'href\s*=\s*"([^"]+)"', re.IGNORECASE)
_ATTR_TITLE_RE = re.compile(r'title\s*=\s*"([^"]+)"', re.IGNORECASE)

# Awesome lists usually write `**Real Title.** [paper](url) [code](url)`, so the
# link text is a generic label and the title sits in the line before the link.
_GENERIC_LINK_TEXT = {
    "paper", "pdf", "[pdf]", "[paper]", "link", "arxiv", "code", "project",
    "page", "slides", "video", "bib", "github", "html", "abs", "openreview",
    "doi", "website", "homepage", "blog", "poster", "supp", "appendix", "demo",
}

# Top-tier venue abbreviations -> canonical display name. Used both to recognize
# a curated venue-list repo by its name and to stamp the parsed venue onto each
# paper (the display names are matched again by registry._is_top_venue).
_VENUE_CANON = {
    "cvpr": "CVPR", "iccv": "ICCV", "eccv": "ECCV", "wacv": "WACV",
    "neurips": "NeurIPS", "nips": "NeurIPS", "icml": "ICML", "iclr": "ICLR",
    "aaai": "AAAI", "ijcai": "IJCAI",
    "acl": "ACL", "emnlp": "EMNLP", "naacl": "NAACL", "coling": "COLING",
    "iros": "IROS", "icra": "ICRA", "corl": "CoRL", "rss": "RSS",
    "kdd": "KDD", "siggraph": "SIGGRAPH",
}
_VENUE_ABBREV_RE = re.compile(r"\b(" + "|".join(_VENUE_CANON) + r")\b")
_YEAR_RE = re.compile(r"20[12]\d")


def _normalize_repo_name(repo_name: str) -> str:
    """Lowercase and split separators / letter-digit runs so 'iccv2025-papers'
    becomes 'iccv 2025 papers' and venue/year tokens match on word boundaries."""
    n = repo_name.lower().replace("-", " ").replace("_", " ")
    n = re.sub(r"(?<=[a-z])(?=\d)", " ", n)  # iccv2025 -> iccv 2025
    n = re.sub(r"(?<=\d)(?=[a-z])", " ", n)  # 2025cvpr -> 2025 cvpr
    return n

# Tokens too generic to use as topic filters against curated venue lists.
_STOPWORDS = {
    "the", "a", "an", "of", "for", "and", "or", "to", "in", "on", "with", "via",
    "using", "based", "toward", "towards", "from", "into", "this", "that",
    "learning", "model", "models", "network", "networks", "deep", "neural",
    "approach", "method", "methods", "framework", "paper", "papers",
}


def _is_paper_url(url: str) -> bool:
    return any(h in url for h in _PAPER_HOSTS)


def _clean_title(text: str) -> str:
    """Strip markdown to recover a plain title from a list-item line fragment."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # [text](url) -> text
    text = re.sub(r"[*_`#>|]", "", text)                   # emphasis / code / table marks
    text = re.sub(r"^\s*[-*+\d.)\]\[\s]+", "", text)       # leading bullets / numbering
    return re.sub(r"\s+", " ", text).strip(" .—-:·")


def _source_id(url: str) -> str:
    m = _ARXIV_RE.search(url)
    if m:
        return f"arxiv:{m.group(1)}"  # so it dedupes with the arXiv source
    return "github:" + re.sub(r"^https?://", "", url)[:80]


def _venue_year_from_repo(repo_name: str):
    """Parse (venue_display, year) from a repo name like 'top-cvpr-2026-papers'.

    Returns (None, None) if the name isn't a recognizable venue list.
    """
    n = _normalize_repo_name(repo_name)
    ym = _YEAR_RE.search(n)
    year = int(ym.group(0)) if ym else None
    vm = _VENUE_ABBREV_RE.search(n)
    if not vm:
        return None, year
    canon = _VENUE_CANON[vm.group(1)]
    return (f"{canon} {year}" if year else canon), year


def _looks_like_venue_list(repo_name: str) -> bool:
    """True for repos like 'top-cvpr-2026-papers' / 'iccv2025-papers'."""
    n = _normalize_repo_name(repo_name)
    return bool(_VENUE_ABBREV_RE.search(n) and _YEAR_RE.search(n))


def _keywords(query: str) -> list:
    """Significant topic tokens from a query, for filtering curated venue lists."""
    toks = re.findall(r"[a-z0-9]+", query.lower())
    return [t for t in toks if len(t) >= 4 and t not in _STOPWORDS]


class GitHubAwesomeSource(PaperSource):
    name = "github"

    def __init__(self, token: str = "", max_repos: int = 3, recent_years: int = 3):
        self._headers = {"Accept": "application/vnd.github+json"}
        if token:
            self._headers["Authorization"] = f"Bearer {token}"
        self._max_repos = max_repos
        self._recent_years = [CURRENT_YEAR - i for i in range(recent_years)]
        # Caches so repeated per-query calls don't refetch/rediscover.
        self._readme_cache: dict = {}
        self._venue_repos = None  # topic-agnostic; discovered once

    def search(self, query: str, limit: int) -> list[Paper]:
        papers: list[Paper] = []
        seen: set = set()

        # 1) Topic-scoped lists (awesome-<topic> / <topic>-papers): take all papers.
        topic_repos = self._search_repos(f"awesome {query} in:name,description")
        topic_repos += self._search_repos(f"{query} papers in:name,description")
        for repo in self._dedup_repos(topic_repos)[: self._max_repos]:
            if len(papers) >= limit:
                break
            venue, year = _venue_year_from_repo(repo.get("name", ""))
            papers.extend(self._papers_from_repo(repo, seen, limit - len(papers),
                                                 venue, year, keywords=None))

        # 2) Curated venue lists (top-<venue>-<year>-papers): filter to the topic.
        if len(papers) < limit:
            kws = _keywords(query)
            for repo in self._venue_list_repos():
                if len(papers) >= limit:
                    break
                venue, year = _venue_year_from_repo(repo.get("name", ""))
                papers.extend(self._papers_from_repo(repo, seen, limit - len(papers),
                                                     venue, year, keywords=kws))

        return papers[:limit]

    def _venue_list_repos(self) -> list:
        """Discover (once) curated conference paper-list repos for recent years.

        Searches like 'top 2026 papers' / '2025 accepted papers' surface repos
        such as `top-cvpr-2026-papers`; we then keep only the ones whose name
        actually encodes a top-tier venue + year.
        """
        if self._venue_repos is not None:
            return self._venue_repos
        found: list = []
        for y in self._recent_years:
            found += self._search_repos(f"top {y} papers in:name")
            found += self._search_repos(f"{y} accepted papers in:name,description")
        repos = [r for r in self._dedup_repos(found)
                 if _looks_like_venue_list(r.get("name", ""))]
        self._venue_repos = repos[: self._max_repos * 3]
        return self._venue_repos

    def _papers_from_repo(self, repo, seen, remaining, venue, year, keywords):
        readme = self._readme(repo.get("full_name", ""))
        if not readme:
            return []
        return self._parse_readme(readme, repo.get("name", ""), seen, remaining,
                                  venue=venue, year=year, keywords=keywords)

    def _search_repos(self, q: str) -> list:
        params = {"q": q, "sort": "stars", "order": "desc", "per_page": self._max_repos}
        resp = get_with_retry(_REPO_SEARCH, params=params, headers=self._headers)
        if resp is None:
            return []
        try:
            return resp.json().get("items") or []
        except ValueError:
            return []

    @staticmethod
    def _dedup_repos(repos: list) -> list:
        seen: set = set()
        out: list = []
        for r in repos:
            fn = r.get("full_name")
            if not fn or fn in seen:
                continue
            seen.add(fn)
            out.append(r)
        return out

    def _readme(self, full_name: str) -> str:
        if not full_name:
            return ""
        if full_name in self._readme_cache:
            return self._readme_cache[full_name]
        md = self._fetch_readme(full_name)
        self._readme_cache[full_name] = md
        return md

    def _fetch_readme(self, full_name: str) -> str:
        headers = dict(self._headers)
        headers["Accept"] = "application/vnd.github.raw"
        resp = get_with_retry(
            f"https://api.github.com/repos/{full_name}/readme", params={}, headers=headers
        )
        return resp.text if resp is not None else ""

    def _parse_readme(self, md: str, repo_name: str, seen: set, remaining: int,
                      *, venue=None, year=None, keywords=None) -> list[Paper]:
        """Extract papers from a README, handling both markdown and HTML formats.

        Markdown lists (`**Title.** [paper](url)`) are parsed line-by-line so the
        real title can be recovered when the link label is generic ('paper').
        Many curated venue lists instead render HTML cards
        (`<a href="arxiv..." title="Real Title">`), so those anchors are mined too.

        If `keywords` is given (curated venue lists), only items mentioning enough
        of the topic keywords are kept (>=2 distinct, or the lone keyword for a
        one-word query), so a topic-agnostic conference list yields only papers
        relevant to the search query rather than flooding the results.
        """
        kw_set = set(keywords) if keywords else set()
        need = 1 if len(kw_set) <= 1 else 2

        def _relevant(text: str) -> bool:
            if not kw_set:
                return True
            low = text.lower()
            return sum(1 for k in kw_set if k in low) >= need

        venue_str = venue or (f"awesome:{repo_name}" if repo_name else "github")

        def _emit(title: str, url: str):
            if len(title) < 8:
                return None
            key = "".join(c for c in title.lower() if c.isalnum())
            if not key or key in seen:
                return None
            seen.add(key)
            return Paper(
                source=self.name,
                source_id=_source_id(url),
                title=title,
                url=url,
                venue=venue_str,
                year=year,
            )

        out: list[Paper] = []

        # Markdown list items — filter on the whole line (title may precede link).
        for line in md.splitlines():
            if len(out) >= remaining:
                return out
            if not _relevant(line):
                continue
            link = next((m for m in _LINK_RE.finditer(line) if _is_paper_url(m.group(2))), None)
            if link is None:
                continue
            label = link.group(1).strip()
            bold = re.search(r"\*\*(.+?)\*\*", line) or re.search(r"__(.+?)__", line)
            if len(label) >= 8 and label.lower() not in _GENERIC_LINK_TEXT:
                title = label
            elif bold:
                # Awesome lists usually bold the title; this dedupes cleanly with arXiv.
                title = _clean_title(bold.group(1))
            else:
                # Fall back to the line text before the link (or the whole cleaned line).
                title = _clean_title(line[: link.start()]) or _clean_title(line)
            paper = _emit(title, link.group(2).strip())
            if paper is not None:
                out.append(paper)

        # HTML anchor cards — `<a href="paper-url" title="Real Title">`.
        for am in _HTML_ANCHOR_RE.finditer(md):
            if len(out) >= remaining:
                break
            attrs = am.group(1)
            href = _ATTR_HREF_RE.search(attrs)
            title_attr = _ATTR_TITLE_RE.search(attrs)
            if not href or not title_attr:
                continue
            url = href.group(1).strip()
            if not _is_paper_url(url):
                continue
            title = _clean_title(title_attr.group(1))
            if not _relevant(title):
                continue
            paper = _emit(title, url)
            if paper is not None:
                out.append(paper)

        return out[:remaining]
