"""Base class for paper sources."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Paper


class PaperSource(ABC):
    """A searchable source of papers (arXiv, Semantic Scholar, ...)."""

    name: str = "base"

    @abstractmethod
    def search(self, query: str, limit: int) -> list[Paper]:
        """Return up to `limit` papers matching `query`.

        Implementations should never raise on network/parse errors — log and
        return an empty list so one flaky source can't sink the whole run.
        """
        raise NotImplementedError
