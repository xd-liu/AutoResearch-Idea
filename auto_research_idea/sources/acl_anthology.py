"""ACL Anthology source — the canonical home for ACL / EMNLP / NAACL (+ Findings).

The Anthology publishes a single page per event (e.g. aclanthology.org/events/
acl-2025/) listing every paper with its title, an inline abstract, and a direct,
open-access PDF (aclanthology.org/<id>.pdf). We fetch each event page once, parse
those out, and filter to the search topic.

Never raises — returns [] on any network/parse error.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime

from ..models import Paper
from ._http import get_with_retry
from .base import PaperSource
from .github_awesome import _keywords

logger = logging.getLogger(__name__)

_BASE = "https://aclanthology.org"
CURRENT_YEAR = datetime.now().year

# Title anchor: href is UNQUOTED in the Anthology markup, e.g.
#   <strong><a class=align-middle href=/2025.acl-long.1/>Title…</a></strong>
_TITLE_RE = re.compile(r"href=/(20\d\d\.[a-z]+(?:-[a-z]+)*\.\d+)/>(.*?)</a></strong>",
                       re.IGNORECASE | re.DOTALL)
# Inline abstract container: <div id=abstract-2025--acl-long--1 …><div class=card-body>…</div>
_ABS_RE = re.compile(r"id=abstract-([0-9a-z-]+)[^>]*>\s*<div class=[\"']?card-body[\"']?[^>]*>(.*?)</div>",
                     re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")

_VENUE_NAMES = [("emnlp", "EMNLP"), ("naacl", "NAACL"), ("acl", "ACL")]
_DEFAULT_VENUES = ["acl", "emnlp", "naacl"]


_SPAN_RE = re.compile(r"</?span[^>]*>", re.IGNORECASE)


def _strip(markup: str) -> str:
    # Drop inline <span> casing wrappers WITHOUT a space (the Anthology wraps
    # single letters: <span class=acl-fixed-case>E</span>com…), then other tags.
    markup = _SPAN_RE.sub("", markup)
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", markup))).strip()


def _venue_label(paper_id: str) -> str:
    # paper_id like '2025.acl-long.1' / '2025.findings-emnlp.3'
    parts = paper_id.split(".")
    year, track = parts[0], (parts[1] if len(parts) > 1 else "")
    base = next((name for key, name in _VENUE_NAMES if key in track), track.upper())
    if track.startswith("findings"):
        return f"{base} {year} Findings"
    return f"{base} {year}"


class ACLAnthologySource(PaperSource):
    name = "acl_anthology"

    def __init__(self, venues=None, recent_years: int = 2, contact_email: str = ""):
        self._venues = venues or _DEFAULT_VENUES
        self._recent_years = [CURRENT_YEAR - i for i in range(max(1, recent_years))]
        self._headers = {"User-Agent": f"auto-research-idea ({contact_email or 'research'})"}
        self._all = None

    def _parse_event(self, page: str) -> list:
        # Build the id -> abstract map in one pass, then pair with titles.
        absmap = {}
        for m in _ABS_RE.finditer(page):
            pid = m.group(1).replace("--", ".")
            absmap[pid] = _strip(m.group(2))
        papers = []
        for m in _TITLE_RE.finditer(page):
            pid = m.group(1)
            if pid.rsplit(".", 1)[-1] == "0":
                continue  # ".0" is the front-matter / whole proceedings
            title = _strip(m.group(2))
            if len(title) < 8:
                continue
            year = int(pid[:4])
            papers.append(Paper(
                source=self.name,
                source_id=f"acl:{pid}",
                title=title,
                abstract=absmap.get(pid, ""),
                year=year,
                venue=_venue_label(pid),
                url=f"{_BASE}/{pid}/",
                landing_url=f"{_BASE}/{pid}/",
                pdf_url=f"{_BASE}/{pid}.pdf",
            ))
        return papers

    def _fetch_all(self) -> list:
        if self._all is not None:
            return self._all
        out = []
        for venue in self._venues:
            for year in self._recent_years:
                url = f"{_BASE}/events/{venue}-{year}/"
                resp = get_with_retry(url, params={}, headers=self._headers, timeout=60, max_attempts=2)
                if resp is not None:
                    out.extend(self._parse_event(resp.text))
        self._all = out
        return out

    def search(self, query: str, limit: int) -> list[Paper]:
        kw = set(_keywords(query))
        need = 1 if len(kw) <= 1 else 2
        out = []
        for p in self._fetch_all():
            if len(out) >= limit:
                break
            if kw:
                text = (p.title + " " + p.abstract).lower()
                if sum(1 for k in kw if k in text) < need:
                    continue
            out.append(p)
        return out[:limit]
