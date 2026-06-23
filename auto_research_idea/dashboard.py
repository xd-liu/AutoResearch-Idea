"""Live dashboard for the research-idea pipeline.

Serves a web page that polls each run's artifacts in `runs/<id>/` and shows live
progress: pipeline step states, the 10 brainstorm variants, the retrieved paper
list, the hybridize key insights, the final ranked ideas with priority, the
adversarial reviews, and the per-step credit summary.

It NEVER runs the pipeline — Claude Code does that. The only things the page
writes are: *queueing* a run (request.json + a 'queued' status.json) and saving
human **annotations** (notes / score / rank overrides) to a non-destructive
`annotations.json` — ideas.json itself is never overwritten. Run the
`research-ideas` skill in Claude Code to pick a queued run up.

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


def _norm_title(t: str) -> str:
    return "".join(c for c in str(t).lower() if c.isalnum())


def _gather_run(run_dir: Path) -> dict:
    status = runstate.load_status(run_dir) or {}
    bs = _read_json(run_dir / "brainstorm.json")
    bs = bs if isinstance(bs, dict) else {}
    papers = _read_json(run_dir / "papers.json")
    papers = papers if isinstance(papers, list) else []
    genes = _pool_dicts(run_dir, "genes*.json")  # genes.json and any genes_<k>.json
    raw = _pool_dicts(run_dir, "ideas_raw*.json")  # one file per parallel hybridizer
    final = _pool_dicts(run_dir, "ideas.json")
    reviews = _pool_dicts(run_dir, "reviews*.json")  # one file per parallel critic
    credit = _read_json(run_dir / "credit_summary.json")
    credit = credit if isinstance(credit, dict) else {}
    annotations = _read_json(run_dir / "annotations.json")
    annotations = annotations if isinstance(annotations, dict) else {}

    # Merge adversarial reviews and human annotations onto each idea (by title).
    rev_by_title = {_norm_title(r.get("title", "")): r for r in reviews if r.get("title")}
    for i in final:
        key = _norm_title(i.get("title", ""))
        if key in rev_by_title:
            i["review"] = rev_by_title[key]
        ann = annotations.get(i.get("title", "")) or annotations.get(key)
        if isinstance(ann, dict):
            i["annotation"] = ann

    # Apply the human rank override (if any) to the display order.
    def _eff_rank(i):
        ann = i.get("annotation") or {}
        for cand in (ann.get("rank"), i.get("rank")):
            try:
                if cand not in (None, ""):
                    return float(cand)
            except (TypeError, ValueError):
                pass
        return 1e9
    final = sorted(final, key=_eff_rank)

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
        "reviews_count": len(reviews),
        "credit": credit,
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
        if path == "/api/run":
            self._post_queue()
            return
        if path == "/api/annotate":
            self._post_annotate()
            return
        self._json({"error": "not found"}, 404)

    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return None

    def _post_queue(self):
        body = self._read_body()
        if body is None:
            self._json({"error": "bad request"}, 400)
            return
        meta = (body.get("meta_idea") or "").strip()
        if not meta:
            self._json({"error": "meta_idea is required"}, 400)
            return
        run_dir = runstate.new_run(RUNS_DIR, meta)
        self._json({"run_id": run_dir.name, "run_dir": str(run_dir)}, 201)

    def _post_annotate(self):
        """Persist a human edit (notes / score / rank) for one idea to
        annotations.json — non-destructive: ideas.json is never overwritten."""
        body = self._read_body()
        if body is None:
            self._json({"error": "bad request"}, 400)
            return
        d = _safe_run_dir((body.get("run_id") or "").strip())
        title = (body.get("title") or "").strip()
        if d is None or not title:
            self._json({"error": "run_id and title are required"}, 400)
            return
        path = d / "annotations.json"
        current = _read_json(path)
        current = current if isinstance(current, dict) else {}
        entry = current.get(title) if isinstance(current.get(title), dict) else {}
        for field in ("notes", "score", "rank"):
            if field in body:
                val = body[field]
                if val in (None, ""):
                    entry.pop(field, None)
                else:
                    entry[field] = val
        if entry:
            current[title] = entry
        else:
            current.pop(title, None)
        tmp = path.parent / (path.name + ".tmp")
        tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        self._json({"ok": True, "title": title, "annotation": entry})


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
.verdict{font-size:11px;font-weight:600;border-radius:99px;padding:1px 8px;text-transform:capitalize}
.v-strong,.v-promising{background:rgba(67,196,107,.16);color:var(--ok)}
.v-needs-work{background:rgba(232,179,57,.16);color:var(--run)}
.v-weak,.v-likely-duplicate{background:rgba(229,83,75,.16);color:var(--err)}
.rev{margin-top:8px;border-top:1px dashed var(--line);padding-top:8px;font-size:13px}
.rev .grp{margin-top:5px}.rev .lab{color:var(--mut);font-weight:600;margin-right:4px}
.rev ul{margin:3px 0 3px 18px;padding:0}.rev li{margin:1px 0}
.rev .good{color:var(--ok)}.rev .bad{color:var(--err)}
.ov{font-size:12px;color:var(--mut)}.ov b{color:var(--fg)}
.anno{margin-top:8px;border-top:1px dashed var(--line);padding-top:8px;display:flex;gap:8px;align-items:flex-start;flex-wrap:wrap}
.anno textarea{flex:1;min-width:220px;background:var(--panel2);border:1px solid var(--line);color:var(--fg);border-radius:6px;padding:6px;font:inherit;resize:vertical;min-height:38px}
.anno input{width:62px;background:var(--panel2);border:1px solid var(--line);color:var(--fg);border-radius:6px;padding:6px}
.anno label{font-size:11px;color:var(--mut);display:block;margin-bottom:2px}
.anno .saved{color:var(--ok);font-size:12px;align-self:center}
.credit{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin-bottom:10px}
.credit .cstep{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:9px}
.credit .cstep .nm{font-weight:600;text-transform:capitalize;font-size:13px}
.credit .net{font-size:18px;font-weight:700}.credit .pos{color:var(--ok)}.credit .neg{color:var(--err)}
.credit .sub{font-size:11px;color:var(--mut)}
.cbanner{background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:10px;margin-bottom:10px;font-size:13px}
.tag-edit{background:rgba(110,168,254,.16);color:var(--acc);font-size:11px;border-radius:99px;padding:1px 8px;margin-left:6px}
</style></head>
<body>
<header><h1>🧬 Research Idea Pipeline</h1><span class="muted" id="hint">live dashboard · auto-refreshes · ideas are editable</span></header>
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
const escA=s=>esc(s).replace(/"/g,"&quot;");
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
  if(d.credit&&d.credit.per_step){h+=renderCredit(d.credit);}
  if(d.ideas&&d.ideas.length){h+=`<h2>Ranked paper ideas (${d.ideas_count})${d.reviews_count?` · ${d.reviews_count} reviewed`:''}</h2>`+
    d.ideas.map(i=>renderIdea(i)).join('');}
  else if(d.raw_count){h+=`<h2>Ranked paper ideas</h2><div class="muted">Generated ${d.raw_count} candidate ideas; awaiting prioritization…</div>`;}
  m.innerHTML=h;
}
function renderCredit(c){
  const steps=['brainstorm','retrieve','digest','hybridize','prioritize'];
  let h=`<h2>Pipeline credit (${c.reviews_count||0} reviews)</h2>`;
  h+=`<div class="cbanner">Weakest step: <b class="bad">${esc(c.weakest_step||'-')}</b> · Strongest: <b class="good">${esc(c.strongest_step||'-')}</b>`+
     `${c.mean_overall_score!=null?` · mean idea score <b>${c.mean_overall_score}</b>/10`:''}`+
     `${c.high_severity_overlaps?` · <span class="bad">${c.high_severity_overlaps} high-severity overlaps</span>`:''}</div>`;
  h+=`<div class="credit">`+steps.map(s=>{const ps=(c.per_step||{})[s]||{};const net=ps.net||0;
    return `<div class="cstep"><div class="nm">${s}</div>
      <div class="net ${net>0?'pos':net<0?'neg':''}">${net>0?'+':''}${net}</div>
      <div class="sub">+${ps.strength||0} strengths · −${ps.weakness||0} weaknesses</div></div>`;}).join('')+`</div>`;
  return h;
}
function renderIdea(i){
  const sc=i.scores||{};const a=i.annotation||{};const r=i.review;
  const dispScore=(a.score!=null&&a.score!=='')?a.score:(sc.total||'-');
  const dispRank=(a.rank!=null&&a.rank!=='')?a.rank:(i.rank||'');
  const edited=(a.rank!=null&&a.rank!=='')||(a.score!=null&&a.score!=='')||(a.notes);
  let h=`<div class="idea"><div class="h">
    <div class="rk">#${esc(String(dispRank))}</div><div class="ti">${esc(i.title)}${edited?'<span class="tag-edit">edited</span>':''}</div>
    <div class="score">N ${sc.novelty||'-'} · F ${sc.feasibility||'-'} · I ${sc.impact||'-'} = <b>${esc(String(dispScore))}</b></div></div>
    <div class="row"><span class="lab">Hypothesis</span>${esc(i.hypothesis)}</div>
    <div class="row"><span class="lab">Method</span>${esc(i.method_sketch)}</div>
    ${i.key_insight?`<div class="row"><span class="lab">Insight</span>${esc(i.key_insight)}</div>`:''}
    ${(i.parent_source_ids||[]).length?`<div class="row muted">from: ${i.parent_source_ids.map(esc).join(', ')}</div>`:''}`;
  if(r){h+=renderReview(r);}
  // Editable human annotation (notes / score / rank), persisted to annotations.json.
  const t=escA(i.title);
  h+=`<div class="anno" data-title="${t}">
    <div style="flex:1;min-width:220px"><label>Your notes</label><textarea oninput="markEditing()" onblur="saveAnno(this)">${esc(a.notes||'')}</textarea></div>
    <div><label>Score</label><input type="number" step="0.5" value="${a.score!=null?esc(String(a.score)):''}" oninput="markEditing()" onblur="saveAnno(this)" data-f="score"></div>
    <div><label>Rank</label><input type="number" step="1" value="${a.rank!=null?esc(String(a.rank)):''}" oninput="markEditing()" onblur="saveAnno(this)" data-f="rank"></div>
    <span class="saved" style="display:none">saved ✓</span></div>`;
  h+=`</div>`;
  return h;
}
function renderReview(r){
  const v=esc(r.verdict||'');const vc='v-'+v.replace(/\s+/g,'-');
  let h=`<div class="rev"><div class="grp"><span class="verdict ${vc}">${v||'reviewed'}</span>`+
    `${r.overall_score!=null?` <span class="muted">critic score ${esc(String(r.overall_score))}/10</span>`:''}</div>`;
  if(r.novelty_assessment)h+=`<div class="grp"><span class="lab">Novelty</span>${esc(r.novelty_assessment)}</div>`;
  if(r.potential_impact)h+=`<div class="grp"><span class="lab">Impact</span>${esc(r.potential_impact)}</div>`;
  if((r.strengths||[]).length)h+=`<div class="grp"><span class="lab good">Strengths</span><ul>`+r.strengths.map(s=>`<li class="good">${esc(s)}</li>`).join('')+`</ul></div>`;
  if((r.weaknesses||[]).length)h+=`<div class="grp"><span class="lab bad">Weaknesses</span><ul>`+r.weaknesses.map(s=>`<li class="bad">${esc(s)}</li>`).join('')+`</ul></div>`;
  if((r.overlap||[]).length)h+=`<div class="grp"><span class="lab">Overlap</span>`+r.overlap.map(o=>`<div class="ov">[${esc(o.severity||'')}] <b>${esc(o.title||o.source_id||'')}</b> — ${esc(o.relation||'')}</div>`).join('')+`</div>`;
  if((r.step_attributions||[]).length)h+=`<div class="grp"><span class="lab">Credit</span>`+r.step_attributions.map(a=>`<div class="ov"><span class="${a.type==='strength'?'good':'bad'}">${esc(a.step||'')} ${a.type==='strength'?'+':'−'}</span> ${esc(a.note||'')}</div>`).join('')+`</div>`;
  return h+`</div>`;
}
let editingUntil=0;
function markEditing(){editingUntil=Date.now()+15000;}
async function saveAnno(el){
  const box=el.closest('.anno');const title=box.getAttribute('data-title');
  const ta=box.querySelector('textarea');const sIn=box.querySelector('[data-f=score]');const rIn=box.querySelector('[data-f=rank]');
  const payload={run_id:sel,title:title,notes:ta.value,score:sIn.value===''?null:Number(sIn.value),rank:rIn.value===''?null:Number(rIn.value)};
  try{await getJSON('/api/annotate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const s=box.querySelector('.saved');if(s){s.style.display='inline';setTimeout(()=>{s.style.display='none';},1500);}
  }catch(e){}
  editingUntil=0;
}
function isEditing(){
  const ae=document.activeElement;
  if(ae&&ae.closest&&ae.closest('.anno'))return true;
  return Date.now()<editingUntil;
}
refreshRuns();setInterval(()=>{refreshRuns();if(!isEditing())refreshRun();},2500);
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
