"""OpenAlex source via the public works API."""

from __future__ import annotations

import logging

from ..models import Paper
from ._http import get_with_retry
from .base import PaperSource

logger = logging.getLogger(__name__)

_API = "https://api.openalex.org/works"


class OpenAlexSource(PaperSource):
    name = "openalex"

    def __init__(self, contact_email: str = ""):
        # OpenAlex grants the faster "polite pool" when you identify yourself.
        self._params_base = {"mailto": contact_email} if contact_email else {}

    @staticmethod
    def _reconstruct_abstract(inverted_index: dict | None) -> str:
        """OpenAlex returns abstracts as an inverted index; rebuild the text."""
        if not inverted_index:
            return ""
        positions: list[tuple[int, str]] = []
        for word, idxs in inverted_index.items():
            for i in idxs:
                positions.append((i, word))
        # Sort by position only (stable) so equal positions keep insertion order
        # instead of being reordered alphabetically by the word.
        positions.sort(key=lambda pair: pair[0])
        return " ".join(word for _, word in positions)

    def _to_paper(self, item: dict) -> Paper:
        authorships = item.get("authorships") or []
        host = (item.get("primary_location") or {}).get("source") or {}
        return Paper(
            source=self.name,
            source_id=f"openalex:{(item.get('id') or '').rsplit('/', 1)[-1]}",
            title=item.get("title") or "",
            abstract=self._reconstruct_abstract(item.get("abstract_inverted_index")),
            authors=[(a.get("author") or {}).get("display_name", "") for a in authorships],
            year=item.get("publication_year"),
            url=item.get("doi") or item.get("id") or "",
            venue=host.get("display_name") or "",
            citation_count=item.get("cited_by_count"),
        )

    def search(self, query: str, limit: int) -> list[Paper]:
        params = {
            **self._params_base,
            "search": query,
            "per-page": min(limit, 50),
            "sort": "relevance_score:desc",
        }
        resp = get_with_retry(_API, params=params)
        if resp is None:
            return []
        return [self._to_paper(item) for item in (resp.json().get("results") or [])]

    def lookup_title(self, title: str, limit: int = 5) -> list[Paper]:
        """Candidate records for a known title — used to enrich abstract-less
        papers (e.g. from venue pages) with abstract / year / citations."""
        params = {**self._params_base, "search": title, "per-page": min(limit, 25)}
        resp = get_with_retry(_API, params=params)
        if resp is None:
            return []
        return [self._to_paper(item) for item in (resp.json().get("results") or [])]
