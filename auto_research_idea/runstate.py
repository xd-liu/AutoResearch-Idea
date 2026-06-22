"""Run-state helper: the contract between the orchestrator and the dashboard.

Each run lives in `runs/<run_id>/`. `status.json` is the live progress record the
dashboard polls; the orchestrator updates it between steps via this module's CLI:

    python -m auto_research_idea.runstate new  --runs-dir runs --meta "my idea"
    python -m auto_research_idea.runstate set  --run-dir runs/<id> --step brainstorm --state running
    python -m auto_research_idea.runstate set  --run-dir runs/<id> --step brainstorm --state done \\
        --summary "10 variants, 11 queries" --artifact brainstorm.json

States: pending | running | done | error  (per step and overall).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

STEPS = ["brainstorm", "retrieve", "digest", "hybridize", "prioritize"]
STATUS_FILE = "status.json"
REQUEST_FILE = "request.json"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _slug(text: str, n: int = 30) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:n] or "run"


def new_run(runs_dir, meta_idea: str) -> Path:
    """Create runs/<id>/ with request.json + an initial status.json. Returns the dir."""
    runs_dir = Path(runs_dir)
    run_id = f"{_slug(meta_idea)}-{uuid.uuid4().hex[:6]}"
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / REQUEST_FILE).write_text(
        json.dumps({"meta_idea": meta_idea, "created_at": _now()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    status = {
        "run_id": run_id,
        "meta_idea": meta_idea,
        "state": "queued",
        "created_at": _now(),
        "updated_at": _now(),
        "steps": [{"name": s, "state": "pending", "summary": "", "artifact": ""} for s in STEPS],
    }
    _write_status(run_dir, status)
    return run_dir


def load_status(run_dir) -> Optional[dict]:
    p = Path(run_dir) / STATUS_FILE
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_status(run_dir, status: dict) -> None:
    # Atomic-ish write so the dashboard never reads a half-written file.
    p = Path(run_dir) / STATUS_FILE
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def set_step(run_dir, step: str, state: str, summary: str = "", artifact: str = "") -> dict:
    status = load_status(run_dir) or {
        "run_id": Path(run_dir).name,
        "meta_idea": "",
        "state": "running",
        "created_at": _now(),
        "updated_at": _now(),
        "steps": [{"name": s, "state": "pending", "summary": "", "artifact": ""} for s in STEPS],
    }
    found = False
    for st in status["steps"]:
        if st["name"] == step:
            st["state"] = state
            if summary:
                st["summary"] = summary
            if artifact:
                st["artifact"] = artifact
            found = True
            break
    if not found:
        status["steps"].append({"name": step, "state": state, "summary": summary, "artifact": artifact})

    states = [s["state"] for s in status["steps"]]
    if "error" in states:
        status["state"] = "error"
    elif all(s == "done" for s in states):
        status["state"] = "done"
    else:
        status["state"] = "running"
    status["updated_at"] = _now()
    _write_status(run_dir, status)
    return status


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Manage a run's status.json.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_new = sub.add_parser("new", help="Create a new run directory.")
    p_new.add_argument("--runs-dir", default="runs")
    p_new.add_argument("--meta", required=True, help="The meta-idea.")

    p_pending = sub.add_parser("pending", help="Print the newest queued run dir, if any.")
    p_pending.add_argument("--runs-dir", default="runs")

    p_set = sub.add_parser("set", help="Set a step's state.")
    p_set.add_argument("--run-dir", required=True)
    p_set.add_argument("--step", required=True, choices=STEPS)
    p_set.add_argument("--state", required=True, choices=["pending", "running", "done", "error"])
    p_set.add_argument("--summary", default="")
    p_set.add_argument("--artifact", default="")

    args = parser.parse_args(argv)

    if args.cmd == "new":
        run_dir = new_run(args.runs_dir, args.meta)
        # Print the run dir so the orchestrator can capture it.
        print(str(run_dir))
        return 0

    if args.cmd == "pending":
        runs = Path(args.runs_dir)
        candidates = []
        if runs.exists():
            for d in runs.iterdir():
                if not d.is_dir():
                    continue
                st = load_status(d)
                if st and st.get("state") == "queued":
                    try:
                        mtime = d.stat().st_mtime  # real creation order, sub-second
                    except OSError:
                        mtime = 0.0
                    candidates.append((mtime, d.name, d))
        if candidates:
            candidates.sort()  # by mtime, then name; newest last
            print(str(candidates[-1][2]))
        return 0

    if args.cmd == "set":
        set_step(args.run_dir, args.step, args.state, args.summary, args.artifact)
        print(f"[runstate] {args.step} -> {args.state}", file=sys.stderr)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
