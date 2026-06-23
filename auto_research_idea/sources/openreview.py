"""OpenReview source — the canonical home for ICLR / NeurIPS / ICML / CoRL.

These top-tier ML venues publish accepted papers on OpenReview, each with title,
abstract, and a direct PDF — exactly what we need. We query the v2 API by
`content.venueid` (which returns the *accepted* papers, not the review notes the
free-text search mixes in), page through them once, and filter to the search
topic locally. arXiv/OpenAlex rarely surface these promptly, so this is what
covers, e.g., ICLR 2026.

Never raises — returns [] on any network/parse error.
"""

from __future__ import annotations

import logging
from datetime import datetime

from ..models import Paper
from ._http import get_with_retry
from .base import PaperSource
from .github_awesome import _keywords

logger = logging.getLogger(__name__)

_API = "https://api2.openreview.net/notes"
_FORUM = "https://openreview.net/forum?id="
_PDF = "https://openreview.net"
CURRENT_YEAR = datetime.now().year

# venueid templates ({year} filled in). Accepted papers carry these as venueid.
_DEFAULT_VENUES = [
    {"name": "ICLR", "id": "ICLR.cc/{year}/Conference"},
    {"name": "NeurIPS", "id": "NeurIPS.cc/{year}/Conference"},
    {"name": "ICML", "id": "ICML.cc/{year}/Conference"},
    {"name": "CoRL", "id": "robot-learning.org/CoRL/{year}/Conference"},
]


def _val(content: dict, key: str):
    v = content.get(key)
    return v.get("value") if isinstance(v, dict) else v


class OpenReviewSource(PaperSource):
    name = "openreview"

    def __init__(self, venues=None, recent_years: int = 2, contact_email: str = "",
                 max_per_venue: int = 6000):
        self._venues = venues or _DEFAULT_VENUES
        self._recent_years = [CURRENT_YEAR - i for i in range(max(1, recent_years))]
        self._max_per_venue = max_per_venue
        self._headers = {"User-Agent": f"auto-research-idea ({contact_email or 'research'})"}
        self._all = None  # cached accepted papers across all venue-years

    def _to_paper(self, note: dict, vname: str, year: int) -> Paper:
        c = note.get("content") or {}
        pdf = _val(c, "pdf") or ""
        if pdf.startswith("/"):
            pdf = _PDF + pdf
        forum = note.get("forum") or note.get("id") or ""
        return Paper(
            source=self.name,
            source_id=f"openreview:{note.get('id', '')}",
            title=_val(c, "title") or "",
            abstract=_val(c, "abstract") or "",
            authors=_val(c, "authors") or [],
            year=year,
            venue=_val(c, "venue") or f"{vname} {year}",
            url=f"{_FORUM}{forum}" if forum else "",
            landing_url=f"{_FORUM}{forum}" if forum else "",
            pdf_url=pdf,
        )

    def _fetch_all(self) -> list:
        if self._all is not None:
            return self._all
        out: list = []
        for v in self._venues:
            for year in self._recent_years:
                vid = v["id"].format(year=year)
                offset = 0
                while offset < self._max_per_venue:
                    resp = get_with_retry(
                        _API,
                        params={"content.venueid": vid, "limit": 1000, "offset": offset},
                        headers=self._headers,
                        max_attempts=2,
                    )
                    if resp is None:
                        break
                    try:
                        notes = resp.json().get("notes") or []
                    except ValueError:
                        break
                    if not notes:
                        break
                    for n in notes:
                        p = self._to_paper(n, v["name"], year)
                        if p.title:
                            out.append(p)
                    if len(notes) < 1000:
                        break
                    offset += 1000
        self._all = out
        return out

    def search(self, query: str, limit: int) -> list[Paper]:
        kw = set(_keywords(query))
        need = 1 if len(kw) <= 1 else 2
        out: list[Paper] = []
        for p in self._fetch_all():
            if len(out) >= limit:
                break
            if kw:
                text = (p.title + " " + p.abstract).lower()
                if sum(1 for k in kw if k in text) < need:
                    continue
            out.append(p)
        return out[:limit]
