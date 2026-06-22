# 🧬 Auto Research Idea

> Turn a one-line research **meta-idea** into a ranked list of **50–100 concrete, novel paper ideas** — each cross-bred from real, freshly-retrieved literature, scored for novelty, feasibility, and impact.

You give it a rough thought. It brainstorms angles, pulls ~50 related papers, distills each into reusable "genes," cross-breeds those genes into fresh ideas (*hybridization*), then scores and ranks them — all while a **live dashboard** shows the work happening step by step.

It runs on **Claude Code** (your Pro/Max subscription — no API key needed for the default pipeline).

```
   "diffusion models for combinatorial optimization"
                      │
   brainstorm ─▶ retrieve ─▶ digest ─▶ hybridize (×N parallel) ─▶ prioritize
    (opus)        (sonnet)   (sonnet)   (opus)                     (opus)
      │             │          │          │                          │
  10 variants    ~50 papers  "genes"   50–100 ideas             scored & ranked
  + queries      dedup+rank  (parallel  + key insights          → ideas.json
                              analysis)
```

---

## ✨ What you get

A ranked `ideas.json` (and a live web view of it), where each idea looks roughly like:

```jsonc
{
  "title": "Annealed Diffusion Samplers for Large-Neighborhood Search in MILP",
  "key_insight": "Treat the diffusion denoiser as a learned neighborhood proposal...",
  "parent_genes": ["gene from paper A", "gene from paper B"],   // what it was bred from
  "scores": { "novelty": 8, "feasibility": 6, "impact": 7 },
  "why_it_might_work": "...",
  "risks": "..."
}
```

The point isn't one perfect idea — it's a **ranked menu** of cross-bred directions you'd never have enumerated by hand, each grounded in papers that actually exist.

---

## 🚀 Quickstart

**1. Install** (Python 3.8 + a handful of deps):

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # optional — see "Keys" below; works without any key
```

**2. Start the dashboard** (separate terminal):

```bash
.venv/bin/python -m auto_research_idea.dashboard      # → http://localhost:8000
```

**3. Run the pipeline** — just talk to **Claude Code** in this directory:

> **use the research-ideas skill on:** diffusion models for combinatorial optimization

Claude Code brainstorms variants, spawns the retrieve/digest subagents, fans out
parallel hybridizers, prioritizes, and updates the dashboard live. Final ranked
ideas land in `runs/<id>/ideas.json` and on the page.

*(Alternatively: type your idea into the dashboard's box to **queue** it, then tell
Claude Code "run the queued research-ideas request".)*

### 🔑 Keys (all optional)

The default pipeline runs entirely on your **Claude Code subscription** — the
subagents digest in-context, so **no `ANTHROPIC_API_KEY` is required**. Keys only
unlock extras / raise rate limits, and all go in `.env` (never `.env.example`):

| Variable | Needed for | Without it |
|---|---|---|
| `ANTHROPIC_API_KEY` | the **standalone** `digest.py` CLI (API credits) | pipeline still works; CLI digest won't |
| `SEMANTIC_SCHOLAR_API_KEY` | higher Semantic Scholar rate limits | S2 source fails soft (other sources still work) |
| `GITHUB_TOKEN` | the `github` awesome-list source | 60 req/hr instead of 5000 |
| `CONTACT_EMAIL` | politeness with arXiv / OpenAlex | slightly stricter rate limits |

---

## 🏗️ How it works

**Skill/subagent = brain; Python = hands.** Creative, judgment, and orchestration
work is done by Claude Code subagents; deterministic, parallel, mechanical work
(multi-source retrieval, dedup, ranking) is Python tools.

- **Orchestrator** — Claude Code running the `research-ideas` skill
  (`.claude/skills/research-ideas/`). Delegates each step to a subagent and tracks
  progress in `runs/<id>/status.json`.
- **One subagent per step** (`.claude/agents/*.md`) — each independently tunable,
  each writes its artifact into the run directory:

  | Step | Agent (model) | Writes | What it does |
  |---|---|---|---|
  | 1 | `idea-brainstormer` (opus) | `brainstorm.json` | 10 idea variants + search queries |
  | 2 | `paper-retriever` (sonnet) | `papers.json` | runs `search` tool → ~50 deduped, ranked papers |
  | 3 | `paper-digester` (sonnet) | `genes.json` | distills each paper into a reusable "gene" |
  | 4 | `idea-hybridizer` (opus, ×N) | `ideas_raw_*.json` | cross-breeds genes → 50–100 candidate ideas |
  | 5 | `idea-prioritizer` (opus) | `ideas.json` | scores, dedups, ranks |

- **Python tools** (`auto_research_idea/`) — `search` (multi-source retrieval +
  dedup + rank across arXiv, OpenAlex, Semantic Scholar, GitHub awesome-lists, …)
  and `digest` (standalone parallel paper analysis).
- **Dashboard** (`dashboard.py`) — read-only page that polls the run artifacts and
  renders variants, papers, key insights, and ranked ideas live. It never runs the
  pipeline.

**Models:** cheap mechanical steps (retrieve, digest) use **Sonnet**; creative
steps (brainstorm, hybridize, prioritize) use **Opus**. Change a step's model in
its `.claude/agents/*.md` file (or `config.yaml` for digest).

---

## ⚙️ Configuration

`config.yaml` tunes the **tools**: paper sources, retrieval limits
(`retrieval.max_papers` ≈ 50), and the digest model/effort. Orchestration knobs
(10 variants, number of parallel hybridizers → 50–100 ideas) live in the skill and
agent files.

---

## 🛠️ Running the Python tools directly (debugging)

The retrieval path needs no key — you can exercise it standalone:

```bash
.venv/bin/python -m auto_research_idea.search --queries "graph neural network CO" --out papers.json
.venv/bin/python -m auto_research_idea.digest --papers papers.json --out genes.json   # needs ANTHROPIC_API_KEY
```

---

## 📁 Project layout

```
.claude/
  skills/research-ideas/SKILL.md     # orchestrator
  agents/                            # one subagent per step
    idea-brainstormer.md  paper-retriever.md  paper-digester.md
    idea-hybridizer.md    idea-prioritizer.md
auto_research_idea/
  search.py        # tool: queries -> papers.json (search + dedup + rank)
  digest.py        # tool: papers.json -> genes.json (parallel analysis)
  dashboard.py     # read-only live web dashboard (stdlib only)
  runstate.py      # run-dir status contract (orchestrator <-> dashboard)
  llm.py models.py config.py
  sources/         # arxiv, openalex, semantic_scholar, github_awesome,
                   #   venue_pages, pdf_extract, _http (backoff), registry
config.yaml  requirements.txt
runs/<id>/         # per-run artifacts (created at runtime; git-ignored)
```

**Extend it:** add a paper source by subclassing `PaperSource` in `sources/`
(it must never raise — return `[]` on failure). Optimize a step by editing its
`.claude/agents/*.md`. Change the run-dir contract in `runstate.py` (the dashboard
reads the same shapes).

---

## 📝 Notes

- **Python 3.8 only** here, with `anthropic` 0.72.0 — the code deliberately avoids
  newer SDK features (`messages.parse`, `int | None` annotations). See `CLAUDE.md`
  for the full constraints if you plan to hack on it.
- No test framework: verify changes by importing the modules and running the real
  (keyless) tools. See `CLAUDE.md` → *Verifying changes*.
