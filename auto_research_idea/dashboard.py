"""Read-only live dashboard for the research-idea pipeline.

Serves a web page that polls each run's artifacts in `runs/<id>/` and shows live
progress: pipeline step states, the 10 brainstorm variants, the retrieved paper
list, the hybridize key insights, and the final ranked ideas with priority.

It NEVER runs the pipeline — Claude Code does that. The page's input box only
*queues* a run (writes request.json + a 'queued' status.json); you then run the
`research-ideas` skill in Claude Code, which picks the queued run up.

    python -m auto_research_idea.dashboard [--port 8000] [--runs-dir runs]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from . import runstate

RUNS_DIR = Path("runs")


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _safe_run_dir(run_id: str) -> Optional[Path]:
    """Resolve a run_id to a direct child of RUNS_DIR (no traversal, no symlink escape)."""
    if not run_id or "/" in run_id or "\\" in run_id or run_id.startswith("."):
        return None
    d = RUNS_DIR / run_id
    if not d.is_dir():
        return None
    try:
        if d.resolve().parent != RUNS_DIR.resolve():
            return None  # a symlink or junction pointing outside runs/
    except OSError:
        return None
    return d


def _list_runs() -> list:
    out = []
    if RUNS_DIR.exists():
        for d in RUNS_DIR.iterdir():
            if not d.is_dir():
                continue
            st = runstate.load_status(d)
            if not st:
                continue
            steps = st.get("steps", [])
            if not isinstance(steps, list):
                steps = []
            done = sum(1 for s in steps if isinstance(s, dict) and s.get("state") == "done")
            out.append({
                "run_id": st.get("run_id", d.name),
                "meta_idea": st.get("meta_idea", ""),
                "state": st.get("state", "?"),
                "updated_at": st.get("updated_at", ""),
                "progress": f"{done}/{len(steps)}",
            })
    out.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
    return out


def _pool_dicts(run_dir: Path, pattern: str) -> list:
    """Concatenate dict items from every JSON-array file matching the glob pattern.

    Artifacts are LLM-written, so guard against non-list files and non-dict items.
    """
    out = []
    for f in sorted(glob.glob(str(run_dir / pattern))):
        data = _read_json(Path(f))
        if isinstance(data, list):
            out.extend(x for x in data if isinstance(x, dict))
    return out


def _gather_run(run_dir: Path) -> dict:
    status = runstate.load_status(run_dir) or {}
    bs = _read_json(run_dir / "brainstorm.json")
    bs = bs if isinstance(bs, dict) else {}
    papers = _read_json(run_dir / "papers.json")
    papers = papers if isinstance(papers, list) else []
    genes = _pool_dicts(run_dir, "genes*.json")  # genes.json and any genes_<k>.json
    raw = _pool_dicts(run_dir, "ideas_raw*.json")  # one file per parallel hybridizer
    final = _pool_dicts(run_dir, "ideas.json")

    # Key insights come from the final ideas if scored, else the raw pool.
    insight_src = final or raw
    insights = [
        {"title": i.get("title", ""), "key_insight": i.get("key_insight", "")}
        for i in insight_src if i.get("key_insight")
    ]

    return {
        "status": status,
        "refined_problem": bs.get("refined_problem", ""),
        "variants": bs.get("variants", []),
        "queries": bs.get("queries", []),
        "papers": papers,
        "papers_count": len(papers),
        "genes_count": len(genes),
        "insights": insights,
        "raw_count": len(raw),
        "ideas": final,
        "ideas_count": len(final),
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _guard(self, fn):
        """Run a route handler; never let an exception escape as a bare 500/reset."""
        try:
            fn()
        except Exception:
            try:
                self._json({"error": "internal"}, 500)
            except Exception:
                pass

    def do_GET(self):
        self._guard(self._route_get)

    def do_POST(self):
        self._guard(self._route_post)

    def _route_get(self):
        path = urlparse(self.path).path
        if path == "/" or path == "/index.html":
            self._send(200, _PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/api/runs":
            self._json(_list_runs())
            return
        if path.startswith("/api/run/"):
            run_id = path[len("/api/run/"):]
            d = _safe_run_dir(run_id)
            if d is None:
                self._json({"error": "not found"}, 404)
                return
            self._json(_gather_run(d))
            return
        self._json({"error": "not found"}, 404)

    def _route_post(self):
        path = urlparse(self.path).path
        if path != "/api/run":
            self._json({"error": "not found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "bad request"}, 400)
            return
        meta = (body.get("meta_idea") or "").strip()
        if not meta:
            self._json({"error": "meta_idea is required"}, 400)
            return
        run_dir = runstate.new_run(RUNS_DIR, meta)
        self._json({"run_id": run_dir.name, "run_dir": str(run_dir)}, 201)


_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Research Idea Pipeline</title>
<style>
:root{--bg:#0f1117;--panel:#171a23;--panel2:#1e222e;--line:#2a2f3d;--fg:#e6e8ee;--mut:#9aa3b2;--acc:#6ea8fe;--ok:#43c46b;--run:#e8b339;--err:#e5534b;--pend:#5b6373}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--fg)}
header{padding:14px 20px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:12px}
header h1{font-size:16px;margin:0;font-weight:600}
.layout{display:flex;height:calc(100vh - 53px)}
aside{width:300px;border-right:1px solid var(--line);overflow:auto;padding:14px}
main{flex:1;overflow:auto;padding:20px;max-width:1100px}
.newrun{display:flex;gap:6px;margin-bottom:14px}
.newrun input{flex:1;background:var(--panel2);border:1px solid var(--line);color:var(--fg);border-radius:6px;padding:8px}
.newrun button,button.mini{background:var(--acc);color:#06122b;border:0;border-radius:6px;padding:8px 10px;font-weight:600;cursor:pointer}
.run{padding:10px;border:1px solid var(--line);border-radius:8px;margin-bottom:8px;cursor:pointer;background:var(--panel)}
.run:hover{border-color:var(--acc)}.run.active{border-color:var(--acc);background:var(--panel2)}
.run .idea{font-weight:600;font-size:13px;margin-bottom:4px}
.run .meta{color:var(--mut);font-size:12px;display:flex;justify-content:space-between}
.badge{display:inline-block;padding:1px 8px;border-radius:99px;font-size:11px;font-weight:600;text-transform:capitalize}
.badge.done{background:rgba(67,196,107,.16);color:var(--ok)}.badge.running{background:rgba(232,179,57,.16);color:var(--run)}
.badge.queued{background:rgba(110,168,254,.16);color:var(--acc)}.badge.error{background:rgba(229,83,75,.16);color:var(--err)}
.badge.pending{background:rgba(91,99,115,.18);color:var(--mut)}
.steps{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0 22px}
.step{flex:1;min-width:150px;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px}
.step .nm{font-weight:600;text-transform:capitalize;display:flex;justify-content:space-between;align-items:center}
.step .sm{color:var(--mut);font-size:12px;margin-top:6px;min-height:16px}
.step.done{border-color:rgba(67,196,107,.4)}.step.running{border-color:rgba(232,179,57,.5)}
h2{font-size:15px;border-bottom:1px solid var(--line);padding-bottom:6px;margin:26px 0 12px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:10px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px}
.card .t{font-weight:600;margin-bottom:4px}.card .a{color:var(--acc);font-size:12px;margin-bottom:6px}
.card .s{color:var(--mut);font-size:13px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--mut);font-weight:600}td a{color:var(--acc);text-decoration:none}
.insight{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--acc);border-radius:6px;padding:10px;margin-bottom:8px}
.insight .t{font-weight:600;font-size:13px}.insight .k{color:var(--mut);font-size:13px;margin-top:3px}
.idea{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px;margin-bottom:10px}
.idea .h{display:flex;align-items:baseline;gap:10px}
.idea .rk{font-size:18px;font-weight:700;color:var(--acc);min-width:30px}
.idea .ti{font-weight:600;font-size:15px;flex:1}
.score{font-size:12px;color:var(--mut);white-space:nowrap}
.idea .row{margin-top:8px;font-size:13px}.idea .lab{color:var(--mut);font-weight:600;margin-right:4px}
.muted{color:var(--mut)}.empty{color:var(--mut);padding:30px;text-align:center}
.pill{font-size:11px;background:var(--panel2);border:1px solid var(--line);border-radius:99px;padding:1px 8px;color:var(--mut);margin:0 4px 4px 0;display:inline-block}
</style></head>
<body>
<header><h1>🧬 Research Idea Pipeline</h1><span class="muted" id="hint">read-only live dashboard · auto-refreshes</span></header>
<div class="layout">
  <aside>
    <div class="newrun">
      <input id="metaInput" placeholder="Queue a new meta-idea…">
      <button onclick="queueRun()">Queue</button>
    </div>
    <div class="muted" style="font-size:12px;margin-bottom:10px">Queue here, then run the <b>research-ideas</b> skill in Claude Code.</div>
    <div id="runs"></div>
  </aside>
  <main id="main"><div class="empty">Select a run on the left, or queue a new meta-idea.</div></main>
</div>
<script>
let sel=null;
const esc=s=>(s||"").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
async function getJSON(u,o){const r=await fetch(u,o);return r.json();}
async function queueRun(){
  const el=document.getElementById('metaInput');const v=el.value.trim();if(!v)return;
  const r=await getJSON('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({meta_idea:v})});
  el.value='';sel=r.run_id;await refreshRuns();await refreshRun();
}
async function refreshRuns(){
  const runs=await getJSON('/api/runs');const box=document.getElementById('runs');
  box.innerHTML=runs.map(r=>`<div class="run ${r.run_id===sel?'active':''}" onclick="pick('${r.run_id}')">
    <div class="idea">${esc(r.meta_idea)||'(no title)'}</div>
    <div class="meta"><span class="badge ${r.state}">${r.state}</span><span>${r.progress}</span></div></div>`).join('')
    ||'<div class="muted">No runs yet.</div>';
}
function pick(id){sel=id;refreshRuns();refreshRun();}
async function refreshRun(){
  if(!sel){return;}
  const d=await getJSON('/api/run/'+encodeURIComponent(sel));const m=document.getElementById('main');
  if(d.error){m.innerHTML='<div class="empty">Run not found.</div>';return;}
  const st=d.status||{};const steps=(st.steps||[]).map(s=>`<div class="step ${s.state}">
     <div class="nm">${s.name}<span class="badge ${s.state}">${s.state}</span></div>
     <div class="sm">${esc(s.summary)}</div></div>`).join('');
  let h=`<h2 style="border:0;margin:0 0 4px">${esc(st.meta_idea)||sel}</h2>
   <div class="muted">${st.state||''} · updated ${esc(st.updated_at||'')}</div>
   <div class="steps">${steps}</div>`;
  if(d.refined_problem)h+=`<h2>Refined problem</h2><div class="card s">${esc(d.refined_problem)}</div>`;
  if(d.variants&&d.variants.length){h+=`<h2>Brainstorm variants (${d.variants.length})</h2><div class="grid">`+
    d.variants.map(v=>`<div class="card"><div class="t">${esc(v.title)}</div><div class="a">${esc(v.angle)}</div><div class="s">${esc(v.summary)}</div></div>`).join('')+`</div>`;
    if(d.queries&&d.queries.length)h+=`<div style="margin-top:10px">`+d.queries.map(q=>`<span class="pill">${esc(q)}</span>`).join('')+`</div>`;}
  if(d.papers_count){h+=`<h2>Papers retrieved (${d.papers_count}) · ${d.genes_count} digested</h2>
   <table><tr><th>Title</th><th>Year</th><th>Venue</th><th>Cites</th></tr>`+
    d.papers.slice(0,80).map(p=>`<tr><td><a href="${esc(p.url)}" target="_blank">${esc(p.title)}</a></td>
     <td>${p.year||''}</td><td>${esc(p.venue)}</td><td>${p.citation_count==null?'':p.citation_count}</td></tr>`).join('')+`</table>`;}
  if(d.insights&&d.insights.length){h+=`<h2>Hybridize key insights (${d.insights.length})</h2>`+
    d.insights.map(i=>`<div class="insight"><div class="t">${esc(i.title)}</div><div class="k">${esc(i.key_insight)}</div></div>`).join('');}
  if(d.ideas&&d.ideas.length){h+=`<h2>Ranked paper ideas (${d.ideas_count})</h2>`+
    d.ideas.map(i=>{const sc=i.scores||{};return `<div class="idea"><div class="h">
      <div class="rk">#${i.rank||''}</div><div class="ti">${esc(i.title)}</div>
      <div class="score">N ${sc.novelty||'-'} · F ${sc.feasibility||'-'} · I ${sc.impact||'-'} = <b>${sc.total||'-'}</b></div></div>
      <div class="row"><span class="lab">Hypothesis</span>${esc(i.hypothesis)}</div>
      <div class="row"><span class="lab">Method</span>${esc(i.method_sketch)}</div>
      ${i.key_insight?`<div class="row"><span class="lab">Insight</span>${esc(i.key_insight)}</div>`:''}
      ${(i.parent_source_ids||[]).length?`<div class="row muted">from: ${i.parent_source_ids.map(esc).join(', ')}</div>`:''}
     </div>`;}).join('');}
  else if(d.raw_count){h+=`<h2>Ranked paper ideas</h2><div class="muted">Generated ${d.raw_count} candidate ideas; awaiting prioritization…</div>`;}
  m.innerHTML=h;
}
refreshRuns();setInterval(()=>{refreshRuns();refreshRun();},2500);
</script>
</body></html>"""


def main(argv=None) -> int:
    global RUNS_DIR
    parser = argparse.ArgumentParser(description="Read-only dashboard for the research-idea pipeline.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--runs-dir", default="runs")
    args = parser.parse_args(argv)

    RUNS_DIR = Path(args.runs_dir)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    print(f"[dashboard] serving {RUNS_DIR}/ at {url}  (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[dashboard] stopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
