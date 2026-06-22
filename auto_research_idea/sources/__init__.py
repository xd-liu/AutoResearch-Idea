"""Pluggable paper-retrieval sources."""

from .base import PaperSource
from .registry import build_sources, search_all

__all__ = ["PaperSource", "build_sources", "search_all"]
