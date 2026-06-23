"""credit tool — aggregate the critic's per-idea step attributions.

The idea-critic subagents tag each idea's strengths/weaknesses with the pipeline
step responsible (brainstorm / retrieve / digest / hybridize / prioritize). This
tool pools every `reviews*.json` in a run and rolls those attributions up into
`credit_summary.json`, so we can see at a glance which step earns the most credit
and which is the weakest link — the signal for improving the tool.

    python -m auto_research_idea.credit --run-dir runs/<id>

Needs no API key; pure aggregation over the review artifacts.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from pathlib import Path

STEPS = ["brainstorm", "retrieve", "digest", "hybridize", "prioritize"]


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _pool_reviews(run_dir: Path) -> list:
    out = []
    for f in sorted(glob.glob(str(run_dir / "reviews*.json"))):
        data = _read_json(Path(f))
        if isinstance(data, list):
            out.extend(x for x in data if isinstance(x, dict))
    return out


def summarize(reviews: list) -> dict:
    per_step = {
        s: {"strength": 0, "weakness": 0, "net": 0, "strength_notes": [], "weakness_notes": []}
        for s in STEPS
    }
    verdicts: dict = {}
    scores = []
    overlap_high = 0
    for r in reviews:
        v = str(r.get("verdict", "")).strip() or "unscored"
        verdicts[v] = verdicts.get(v, 0) + 1
        if isinstance(r.get("overall_score"), (int, float)):
            scores.append(float(r["overall_score"]))
        for o in r.get("overlap", []) or []:
            if isinstance(o, dict) and str(o.get("severity", "")).lower() == "high":
                overlap_high += 1
        for a in r.get("step_attributions", []) or []:
            if not isinstance(a, dict):
                continue
            step = a.get("step")
            kind = a.get("type")
            if step not in per_step or kind not in ("strength", "weakness"):
                continue
            per_step[step][kind] += 1
            note = (a.get("note") or "").strip()
            if note and len(per_step[step][f"{kind}_notes"]) < 5:
                per_step[step][f"{kind}_notes"].append(note)
    for s in STEPS:
        per_step[s]["net"] = per_step[s]["strength"] - per_step[s]["weakness"]

    ranked = sorted(STEPS, key=lambda s: per_step[s]["net"])
    return {
        "reviews_count": len(reviews),
        "mean_overall_score": round(sum(scores) / len(scores), 2) if scores else None,
        "verdicts": verdicts,
        "high_severity_overlaps": overlap_high,
        "per_step": per_step,
        "weakest_step": ranked[0] if ranked else None,
        "strongest_step": ranked[-1] if ranked else None,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate critic step-attributions into credit_summary.json.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--out", default=None, help="Defaults to <run-dir>/credit_summary.json")
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir)
    reviews = _pool_reviews(run_dir)
    summary = summarize(reviews)

    out = Path(args.out) if args.out else run_dir / "credit_summary.json"
    tmp = out.parent / (out.name + ".tmp")
    tmp.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, out)
    print(
        f"[credit] {summary['reviews_count']} reviews -> weakest: {summary['weakest_step']}, "
        f"strongest: {summary['strongest_step']}, mean score {summary['mean_overall_score']} "
        f"(written to {out})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
