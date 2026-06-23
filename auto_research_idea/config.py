"""Configuration loading: merges config.yaml with environment (.env) secrets.

Used by the Python tools. `search` needs no API key; `digest` does (it makes
parallel model calls), so the key is loaded if present but never required at
load time — the digest tool checks for it explicitly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import yaml
from dotenv import load_dotenv

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


@dataclass
class Config:
    models: Dict[str, str] = field(default_factory=dict)
    effort: Dict[str, str] = field(default_factory=dict)
    sources: List[str] = field(default_factory=lambda: ["arxiv"])
    retrieval: Dict[str, Any] = field(default_factory=dict)
    venue_pages: Dict[str, Any] = field(default_factory=dict)
    openreview: Dict[str, Any] = field(default_factory=dict)
    acl_anthology: Dict[str, Any] = field(default_factory=dict)

    # Secrets / environment
    anthropic_api_key: str = ""
    semantic_scholar_api_key: str = ""
    github_token: str = ""
    contact_email: str = ""

    def model_for(self, stage: str) -> str:
        return self.models.get(stage, "claude-opus-4-8")

    def effort_for(self, stage: str) -> str:
        return self.effort.get(stage, "high")


def load_config(path=None) -> Config:
    """Load config.yaml and overlay secrets from the environment."""
    load_dotenv()

    path = Path(path) if path else DEFAULT_CONFIG_PATH
    raw: Dict[str, Any] = {}
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

    return Config(
        models=raw.get("models", {}),
        effort=raw.get("effort", {}),
        sources=raw.get("sources", ["arxiv"]),
        retrieval=raw.get("retrieval", {}),
        venue_pages=raw.get("venue_pages", {}),
        openreview=raw.get("openreview", {}),
        acl_anthology=raw.get("acl_anthology", {}),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", "").strip(),
        semantic_scholar_api_key=os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "").strip(),
        github_token=os.environ.get("GITHUB_TOKEN", "").strip(),
        contact_email=os.environ.get("CONTACT_EMAIL", "").strip(),
    )
