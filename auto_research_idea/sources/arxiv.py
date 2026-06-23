"""arXiv source via the public Atom query API."""

from __future__ import annotations

import logging
import re
import time

import feedparser

from ..models import Paper
from ._http import get_with_retry
from .base import PaperSource

logger = logging.getLogger(__name__)

_API = "http://export.arxiv.org/api/query"


class ArxivSource(PaperSource):
    name = "arxiv"

    def __init__(self, contact_email: str = ""):
        self._headers = {"User-Agent": f"auto-research-idea ({contact_email})"}

    def _parse_feed(self, text: str) -> list[Paper]:
        feed = feedparser.parse(text)
        papers: list[Paper] = []
        for entry in feed.entries:
            arxiv_id = entry.get("id", "").rsplit("/", 1)[-1]
            arxiv_id = re.sub(r"v\d+$", "", arxiv_id)  # drop version for a stable id
            year = None
            if entry.get("published"):
                try:
                    year = int(entry.published[:4])
                except ValueError:
                    pass
            papers.append(
                Paper(
                    source=self.name,
                    source_id=f"arxiv:{arxiv_id}",
                    title=" ".join(entry.get("title", "").split()),
                    abstract=" ".join(entry.get("summary", "").split()),
                    authors=[a.get("name", "") for a in entry.get("authors", [])],
                    year=year,
                    url=entry.get("link", ""),
                    landing_url=f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else "",
                    pdf_url=f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else "",
                    venue="arXiv",
                )
            )
        return papers

    def search(self, query: str, limit: int) -> list[Paper]:
        params = {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": limit,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
        resp = get_with_retry(_API, params=params, headers=self._headers)
        if resp is None:
            return []
        papers = self._parse_feed(resp.text)
        # arXiv asks callers to be gentle.
        time.sleep(0.5)
        return papers

    def lookup_title(self, title: str, limit: int = 5) -> list[Paper]:
        """Candidate records for a known title (abstract-enrichment fallback)."""
        cleaned = re.sub(r"[^A-Za-z0-9 ]", " ", title).strip()
        if not cleaned:
            return []
        params = {
            "search_query": f"ti:{cleaned}",
            "start": 0,
            "max_results": limit,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
        # Fewer attempts than a primary search: this is a best-effort enrichment
        # lookup, often run in bulk, so we don't want to spend ~14s of backoff per
        # title once arXiv starts rate-limiting.
        resp = get_with_retry(_API, params=params, headers=self._headers, max_attempts=2)
        if resp is None:
            return []
        papers = self._parse_feed(resp.text)
        time.sleep(0.5)
        return papers
