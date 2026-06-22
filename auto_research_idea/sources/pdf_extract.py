"""Best-effort PDF text extraction for the optional intro/conclusion enrichment.

The digester works from abstracts, but an intro + conclusion give it much more to
cross-breed on. When a paper has a reachable PDF (arXiv preprint or an
openaccess.thecvf / *.pdf link), we download it and pull those two sections out
heuristically. Brand-new papers often have no PDF yet — then this yields nothing
and the abstract carries the load. Nothing here ever raises.
"""

from __future__ import annotations

import io
import logging
import re

from ._http import get_with_retry

logger = logging.getLogger(__name__)

_ARXIV_ID_RE = re.compile(r"arxiv[:/](\d{4}\.\d{4,5})", re.IGNORECASE)
_ARXIV_ABS_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})", re.IGNORECASE)

_PDF_HEADERS = {"User-Agent": "auto-research-idea", "Accept": "application/pdf"}

# Section headers, allowing a leading number ("1. Introduction", "5 Conclusion").
# The "x ?" forms tolerate pypdf mangling small-caps headings ("INTRODUCTION" ->
# "I NTRODUCTION") by allowing a stray space after the first letter.
_INTRO_RE = re.compile(r"(?im)^\s*(?:\d+\.?\s*)?i ?ntroduction\b.*$")
_CONCLUSION_RE = re.compile(
    r"(?im)^\s*(?:\d+\.?\s*)?(?:c ?onclusions?|c ?oncluding\s+remarks?)\b.*$"
)
# Where a captured section should stop: the next top-level heading.
_NEXT_HEADING_RE = re.compile(
    r"(?im)^\s*(?:\d+\.?\s+\S.{0,60}"
    r"|r ?eferences|a ?cknowledg\w*|a ?ppendix|r ?elated\s+work)\s*$"
)


def pdf_url_for(paper) -> str:
    """Derive a downloadable PDF URL for a paper, or "" if none is apparent."""
    sid = paper.source_id or ""
    url = paper.url or ""
    m = _ARXIV_ID_RE.search(sid) or _ARXIV_ABS_RE.search(url)
    if m:
        return f"https://arxiv.org/pdf/{m.group(1)}"
    if url.lower().endswith(".pdf"):
        return url
    if "openaccess.thecvf.com" in url and "/html/" in url:
        # CVF landing pages mirror a PDF under /papers/ with the same stem.
        return url.replace("/html/", "/papers/").rsplit(".", 1)[0] + ".pdf"
    return ""


def _clean(text: str) -> str:
    text = re.sub(r"-\n", "", text)          # de-hyphenate line breaks
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_section(text: str, start_re: re.Pattern, max_chars: int) -> str:
    m = start_re.search(text)
    if not m:
        return ""
    start = m.end()
    nxt = _NEXT_HEADING_RE.search(text, start)
    end = nxt.start() if nxt else min(len(text), start + max_chars * 3)
    return _clean(text[start:end])[:max_chars]


def fetch_sections(pdf_url: str, *, intro_chars: int = 3500, concl_chars: int = 2000) -> dict:
    """Download a PDF and return {'intro': ..., 'conclusion': ...} (best effort)."""
    if not pdf_url:
        return {"intro": "", "conclusion": ""}
    resp = get_with_retry(pdf_url, params={}, headers=_PDF_HEADERS, timeout=45, max_attempts=2)
    if resp is None:
        return {"intro": "", "conclusion": ""}
    try:
        from pypdf import PdfReader  # imported lazily so the dep is optional

        reader = PdfReader(io.BytesIO(resp.content))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as e:  # corrupt PDF, missing dep, etc. — never crash the run
        logger.warning("PDF parse failed for %s: %s", pdf_url, e)
        return {"intro": "", "conclusion": ""}
    return {
        "intro": _extract_section(text, _INTRO_RE, intro_chars),
        "conclusion": _extract_section(text, _CONCLUSION_RE, concl_chars),
    }
