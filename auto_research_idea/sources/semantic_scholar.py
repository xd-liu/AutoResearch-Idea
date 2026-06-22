"""Semantic Scholar source via the Graph API."""

from __future__ import annotations

import logging

from ..models import Paper
from ._http import get_with_retry
from .base import PaperSource

logger = logging.getLogger(__name__)

_API = "https://api.semanticscholar.org/graph/v1/paper/search"
_FIELDS = "title,abstract,year,authors,url,venue,citationCount,externalIds"


class SemanticScholarSource(PaperSource):
    name = "semantic_scholar"

    def __init__(self, api_key: str = ""):
        self._headers = {"x-api-key": api_key} if api_key else {}

    def search(self, query: str, limit: int) -> list[Paper]:
        params = {"query": query, "limit": limit, "fields": _FIELDS}
        resp = get_with_retry(_API, params=params, headers=self._headers)
        if resp is None:
            return []

        data = resp.json().get("data") or []
        papers: list[Paper] = []
        for item in data:
            papers.append(
                Paper(
                    source=self.name,
                    source_id=f"s2:{item.get('paperId', '')}",
                    title=item.get("title") or "",
                    abstract=item.get("abstract") or "",
                    authors=[a.get("name", "") for a in (item.get("authors") or [])],
                    year=item.get("year"),
                    url=item.get("url") or "",
                    venue=item.get("venue") or "",
                    citation_count=item.get("citationCount"),
                )
            )
        return papers
