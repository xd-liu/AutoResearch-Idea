"""search tool — multi-source paper retrieval, dedup, and ranking.

Run by the orchestrator skill. Needs no API key.

    python -m auto_research_idea.search --queries "q1" "q2" --out papers.json
    python -m auto_research_idea.search --queries-file queries.json --out papers.json

`--queries-file` is a JSON array of strings, or an object {"queries": [...]}.
Output is a JSON array of paper objects written to --out (default: stdout);
a one-line summary is printed to stderr. Honors config.yaml: sources,
retrieval.per_query_limit, retrieval.max_papers, retrieval.min_year.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Optional

from .config import load_config
from .sources import build_sources, search_all


def _atomic_write(path: Path, text: str) -> None:
    """Write via a temp file + rename so a concurrent reader (the dashboard polls
    these) never sees a truncated file."""
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _coerce_queries(data) -> Optional[List[str]]:
    """Normalize parsed JSON into a query list, or None if it's the wrong shape.

    Accepts a list of strings or {"queries": [...]}. Returns None for scalars or
    bare strings so callers don't iterate a string character-by-character.
    """
    if isinstance(data, dict):
        data = data.get("queries", [])
    if not isinstance(data, list):
        return None
    return [str(q).strip() for q in data if str(q).strip()]


def _load_queries(args) -> List[str]:
    if args.queries:
        return [q.strip() for q in args.queries if q.strip()]
    if args.queries_file:
        data = json.loads(Path(args.queries_file).read_text(encoding="utf-8"))
        qs = _coerce_queries(data)
        if qs is None:
            print("error: --queries-file must be a JSON array of strings or "
                  '{"queries": [...]}', file=sys.stderr)
            return []
        return qs
    # Fall back to stdin (JSON array/object, or newline-separated text).
    if not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
        if raw:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                return [line.strip() for line in raw.splitlines() if line.strip()]
            qs = _coerce_queries(data)
            if qs is not None:
                return qs
            return [line.strip() for line in raw.splitlines() if line.strip()]
    return []


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Retrieve, dedupe, and rank papers across sources.")
    parser.add_argument("--queries", nargs="*", help="Search queries (space-separated, each quoted).")
    parser.add_argument("--queries-file", help="JSON file with queries (array, or {\"queries\": [...]}).")
    parser.add_argument("--out", help="Write papers JSON here (default: stdout).")
    parser.add_argument("--config", help="Path to config.yaml.")
    parser.add_argument("--max-papers", type=int, help="Override retrieval.max_papers.")
    args = parser.parse_args(argv)

    queries = _load_queries(args)
    if not queries:
        print("error: no queries provided (use --queries, --queries-file, or stdin)", file=sys.stderr)
        return 2

    cfg = load_config(args.config)
    ret = cfg.retrieval
    sources = build_sources(cfg)

    papers = search_all(
        sources,
        queries,
        per_query_limit=ret.get("per_query_limit", 12),
        max_papers=args.max_papers or ret.get("max_papers", 30),
        min_year=ret.get("min_year", 0),
        enrich_abstracts=ret.get("enrich_abstracts", True),
        parse_pdf=ret.get("parse_pdf", True),
        contact_email=cfg.contact_email,
    )

    payload = [p.model_dump() for p in papers]
    out_text = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.out:
        _atomic_write(Path(args.out), out_text)
    else:
        print(out_text)

    src_names = ", ".join(s.name for s in sources)
    print(
        f"[search] {len(queries)} queries x [{src_names}] -> {len(papers)} papers"
        + (f" written to {args.out}" if args.out else ""),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
