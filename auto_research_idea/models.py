"""Data models for the Python tools layer.

These tools (`search`, `digest`) are the deterministic 'hands' of the system;
the creative orchestration (brainstorm / hybridize / critique) is done by the
skill (Claude Code) itself, so only the retrieval and digestion shapes live here.

`PaperGene`'s JSON schema is embedded in the digest prompt and the model's reply
is validated back into it (see ``llm.LLMClient.parse``). Keep these shapes flat
and plainly typed so the schema is easy for the model to follow.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class Paper(BaseModel):
    """A retrieved paper, normalized across sources."""

    source: str = Field(description="Which source returned this paper, e.g. 'arxiv'.")
    source_id: str = Field(description="The source's native identifier.")
    title: str
    abstract: str = ""
    authors: List[str] = Field(default_factory=list)
    year: Optional[int] = None
    url: str = ""
    venue: str = ""
    citation_count: Optional[int] = None
    # Reusable provenance for re-fetching this paper later: the official landing
    # page (e.g. the CVF poster page / OpenReview forum) and a direct PDF link.
    landing_url: str = ""
    pdf_url: str = ""
    # Optional richer content parsed from the paper PDF when one is available
    # (see sources/pdf_extract.py). Empty when no PDF could be fetched/parsed.
    intro: str = ""
    conclusion: str = ""

    # Tolerate explicit JSON null on string/list fields (hand-written or
    # third-party papers.json) instead of failing the whole batch.
    @field_validator("source", "source_id", "title", "abstract", "url", "venue",
                     "landing_url", "pdf_url", "intro", "conclusion", mode="before")
    @classmethod
    def _str_none_to_empty(cls, v):
        return "" if v is None else v

    @field_validator("authors", mode="before")
    @classmethod
    def _authors_none_to_list(cls, v):
        return [] if v is None else v

    def dedup_key(self) -> str:
        """Normalized title used to merge the same paper across sources."""
        return "".join(c for c in self.title.lower() if c.isalnum())


class PaperGene(BaseModel):
    """The reusable 'genetic material' extracted from one paper."""

    source_id: str = Field(description="Identifier of the paper this gene came from.")
    title: str
    core_idea: str = Field(description="The paper's central contribution in one or two sentences.")
    method: str = Field(description="How it works — the key methodological mechanism.")
    techniques: List[str] = Field(description="Named techniques, components, or tricks worth reusing.")
    limitations: List[str] = Field(description="Weaknesses, gaps, or unaddressed cases.")
    transferable_concepts: List[str] = Field(
        description="Ideas from this paper that could transplant into a different problem."
    )
