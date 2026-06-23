"""ECVA source — open-access ECCV papers (ecva.net).

ECCV is biennial (even years); ECVA hosts every paper's open-access PDF on one
page (ecva.net/papers.php). We parse out title + landing page + direct PDF and
keep the recent editions. Abstracts are backfilled by the registry's OpenAlex/
arXiv enrichment (ECCV is well indexed there). Never raises.
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

_BASE = "https://www.ecva.net/"
_PAPERS_URL = "https://www.ecva.net/papers.php"
CURRENT_YEAR = datetime.now().year

# Each entry: <dt class="ptitle"><br><a href=papers/eccv_2024/.../X_ECCV_2024_paper.php>
# Title</a></dt><dd>authors</dd><dd>[<a href='papers/eccv_2024/.../00004.pdf'>pdf</a>]
_ENTRY_RE = re.compile(
    r'ptitle"><br>\s*<a href=(papers/eccv_(20\d\d)/[^\s>]+?\.php)>(.*?)</a>'
    r".*?href=['\"]?(papers/eccv_20\d\d/[^'\"\s>]+?\.pdf)",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip(markup: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", markup))).strip()


class ECVASource(PaperSource):
    name = "ecva"

    def __init__(self, recent_years: int = 2, contact_email: str = ""):
        # ECCV is biennial, so keep everything from (current_year - recent_years)
        # onward rather than only current_year-1 (which is never an ECCV year).
        self._min_year = CURRENT_YEAR - max(1, recent_years)
        self._headers = {"User-Agent": f"auto-research-idea ({contact_email or 'research'})"}
        self._all = None

    def _fetch_all(self) -> list:
        if self._all is not None:
            return self._all
        out = []
        resp = get_with_retry(_PAPERS_URL, params={}, headers=self._headers, timeout=60, max_attempts=2)
        if resp is not None:
            seen = set()
            for m in _ENTRY_RE.finditer(resp.text):
                landing, year, title_markup, pdf = m.groups()
                year = int(year)
                if year < self._min_year or pdf.endswith("-supp.pdf"):
                    continue
                title = _strip(title_markup)
                key = "".join(c for c in title.lower() if c.isalnum())
                if len(title) < 8 or key in seen:
                    continue
                seen.add(key)
                out.append(Paper(
                    source=self.name,
                    source_id=f"ecva:{pdf.rsplit('/', 1)[-1]}:{year}",
                    title=title,
                    year=year,
                    venue=f"ECCV {year}",
                    url=_BASE + landing,
                    landing_url=_BASE + landing,
                    pdf_url=_BASE + pdf,
                ))
        self._all = out
        return out

    def search(self, query: str, limit: int) -> list[Paper]:
        kw = set(_keywords(query))
        need = 1 if len(kw) <= 1 else 2
        out = []
        for p in self._fetch_all():
            if len(out) >= limit:
                break
            if kw and sum(1 for k in kw if k in p.title.lower()) < need:
                continue
            out.append(p)
        return out[:limit]
