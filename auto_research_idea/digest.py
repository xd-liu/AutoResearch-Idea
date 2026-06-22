"""digest tool — parallel analysis of papers into reusable 'genes'.

Run by the orchestrator skill. Needs ANTHROPIC_API_KEY (it makes one model call
per paper, fanned out across a thread pool).

    python -m auto_research_idea.digest --papers papers.json --out genes.json
    python -m auto_research_idea.digest --papers papers.json --workers 8

Input is the JSON array produced by the search tool (each item needs at least
source_id, title, abstract; year/venue used if present). Output is a JSON array
of gene objects written to --out (default: stdout); progress to stderr.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Optional

from .config import Config, load_config
from .llm import LLMClient
from .models import Paper, PaperGene

logger = logging.getLogger(__name__)

_SYSTEM = """You are a meticulous research analyst. Given a paper's title and \
abstract, you distill its essence into reusable components: its core idea, its \
method, the named techniques worth borrowing, its limitations, and the concepts \
that could transplant into a different problem. Be specific and faithful to the \
text; do not invent results that aren't supported by the abstract."""

_PROMPT = """Analyze this paper.

Title: {title}
Year: {year}
Venue: {venue}
Abstract: {abstract}{extra}

Extract its reusable "genetic material". Set source_id to exactly: {source_id}"""


def _digest_one(llm: LLMClient, cfg: Config, paper: Paper) -> Optional[PaperGene]:
    extra = ""
    if getattr(paper, "intro", ""):
        extra += f"\n\nIntroduction (excerpt):\n{paper.intro}"
    if getattr(paper, "conclusion", ""):
        extra += f"\n\nConclusion (excerpt):\n{paper.conclusion}"
    prompt = _PROMPT.format(
        title=paper.title,
        year=paper.year or "unknown",
        venue=paper.venue or "unknown",
        abstract=paper.abstract or "(no abstract available)",
        extra=extra,
        source_id=paper.source_id,
    )
    try:
        gene = llm.parse(
            model=cfg.model_for("digest"),
            effort=cfg.effort_for("digest"),
            system=_SYSTEM,
            prompt=prompt,
            schema=PaperGene,
        )
    except Exception as e:
        logger.warning("Failed to digest %s: %s", paper.source_id, e)
        return None
    gene.source_id = paper.source_id  # pin to the real paper
    return gene


def digest_papers(
    llm: LLMClient, cfg: Config, papers: List[Paper], *, max_workers: int = 6
) -> List[PaperGene]:
    """Digest papers concurrently; skips papers with no usable abstract."""
    usable = [p for p in papers if p.abstract.strip()]
    genes: List[PaperGene] = []
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_digest_one, llm, cfg, p) for p in usable]
        for fut in as_completed(futures):
            done += 1
            gene = fut.result()
            if gene is not None:
                genes.append(gene)
            print(f"[digest] {done}/{len(usable)}", end="\r", file=sys.stderr)
    print("", file=sys.stderr)
    return genes


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Digest papers into reusable 'genes' (parallel).")
    parser.add_argument("--papers", required=True, help="Papers JSON file (from the search tool).")
    parser.add_argument("--out", help="Write genes JSON here (default: stdout).")
    parser.add_argument("--config", help="Path to config.yaml.")
    parser.add_argument("--workers", type=int, default=6, help="Parallel digestion workers.")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    if not cfg.anthropic_api_key:
        print("error: ANTHROPIC_API_KEY not set (needed for the digest tool).", file=sys.stderr)
        return 2

    raw = json.loads(Path(args.papers).read_text(encoding="utf-8"))
    papers: List[Paper] = []
    for item in raw:
        try:
            papers.append(Paper.model_validate(item))
        except Exception as e:  # skip one malformed record, don't abort the batch
            logger.warning("skipping malformed paper record: %s", e)
    if not papers:
        print("error: no valid papers in input.", file=sys.stderr)
        return 2

    llm = LLMClient(cfg.anthropic_api_key)
    genes = digest_papers(llm, cfg, papers, max_workers=args.workers)

    out_text = json.dumps([g.model_dump() for g in genes], indent=2, ensure_ascii=False)
    if args.out:
        tmp = Path(args.out + ".tmp")
        tmp.write_text(out_text, encoding="utf-8")
        os.replace(tmp, args.out)
    else:
        print(out_text)

    skipped = sum(1 for p in papers if not p.abstract.strip())
    digestible = len(papers) - skipped
    print(
        f"[digest] {len(genes)}/{digestible} digested"
        + (f", {skipped} skipped (no abstract)" if skipped else "")
        + (f" written to {args.out}" if args.out else ""),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
