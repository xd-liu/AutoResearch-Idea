# 🧬 Auto Research Idea

**A literature-grounded idea engine for AI/CS research.** Give it a one-line
research direction; get back a *ranked portfolio* of 50–100 concrete paper ideas —
each one recombined from genes distilled out of real, freshly-retrieved papers and
scored for novelty, feasibility, and impact.

<p align="center">
  <img alt="Python 3.8" src="https://img.shields.io/badge/python-3.8-blue.svg">
  <img alt="Runtime: Claude Code" src="https://img.shields.io/badge/runtime-Claude%20Code-8A2BE2.svg">
  <img alt="Multi-agent" src="https://img.shields.io/badge/architecture-multi--agent-orange.svg">
  <img alt="Status: research preview" src="https://img.shields.io/badge/status-research%20preview-green.svg">
</p>

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

## 💡 Why this exists

Most research novelty is **recombination** — new work tends to pair an unusual
combination of otherwise-conventional prior ideas (cf. literature-based discovery,
Swanson's undiscovered-public-knowledge "ABC" model; *atypical combinations*,
Uzzi et al., *Science* 2013). The bottleneck isn't generating *a* combination —
it's systematically *enumerating and triaging* the combinatorial space of
plausible ones across a literature you can't fully hold in your head.

Auto Research Idea operationalizes that:

1. **Retrieve** the relevant slice of literature (multi-source, deduped, ranked).
2. **Distill** each paper into a reusable **gene** — its core mechanism, the
   assumption it relies on, where it breaks.
3. **Recombine** genes across papers into candidate ideas, each with an explicit
   *key insight* and *parent genes* (full provenance — you can trace every idea
   back to the papers it came from).
4. **Score & rank** on novelty / feasibility / impact, dedup near-twins, and
   surface a portfolio.

The output isn't one "perfect" idea — it's a **ranked menu of grounded
directions** you'd never have enumerated by hand, with the receipts to vet each.

---

## ✨ What you get

A ranked `ideas.json` (and a live web view of it). Each idea carries its lineage:

```jsonc
{
  "title": "Annealed Diffusion Samplers for Large-Neighborhood Search in MILP",
  "key_insight": "Treat the diffusion denoiser as a learned neighborhood proposal
                  distribution, annealing temperature to trade exploration vs. repair...",
  "parent_genes": ["gene from paper A", "gene from paper B"],   // provenance
  "scores": { "novelty": 8, "feasibility": 6, "impact": 7 },
  "why_it_might_work": "...",
  "risks": "..."
}
```

Every run is **reproducible and inspectable**: all intermediate artifacts
(variants, retrieved papers, genes, raw candidates, final ranking) are written as
JSON under `runs/<id>/` and rendered live by the dashboard.

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

## 🧪 A run, end to end

| Stage | What happens | Artifact |
|---|---|---|
| **Brainstorm** | seed idea → 10 distinct framings + targeted search queries | `brainstorm.json` |
| **Retrieve** | queries fan out across arXiv, OpenAlex, Semantic Scholar, GitHub awesome-lists → merge, dedup by title, rank | `papers.json` |
| **Digest** | each paper → a structured *gene*: mechanism, assumption, failure mode | `genes.json` |
| **Hybridize** | N Opus agents in parallel cross-breed genes → candidate ideas with key insight + provenance | `ideas_raw_*.json` |
| **Prioritize** | score (novelty/feasibility/impact), dedup near-duplicates, rank | `ideas.json` |

Because each stage is a file, you can **stop, inspect, edit, and resume** — e.g.
hand-curate `genes.json` before hybridizing, or re-run prioritization with
different weights.

---

## 🏗️ System design (for the agent-systems crowd)

The interesting bit isn't just the output — it's the orchestration pattern:

**Skill/subagent = brain; Python = hands.** Open-ended creative + judgment work is
delegated to LLM subagents; deterministic, parallel, mechanical work (retrieval,
dedup, ranking) is plain Python. Each side does what it's good at.

- **Orchestrator** — Claude Code running the `research-ideas` skill
  (`.claude/skills/research-ideas/`). Delegates each step to a subagent and tracks
  progress in `runs/<id>/status.json`.
- **One subagent per step** (`.claude/agents/*.md`) — independently promptable and
  swappable, with **task-based model routing**: cheap mechanical steps run on
  **Sonnet**, open-ended creative steps on **Opus**.

  | Step | Agent (model) | Writes | Role |
  |---|---|---|---|
  | 1 | `idea-brainstormer` (opus) | `brainstorm.json` | 10 idea variants + search queries |
  | 2 | `paper-retriever` (sonnet) | `papers.json` | drives `search` tool → ~50 deduped, ranked papers |
  | 3 | `paper-digester` (sonnet) | `genes.json` | distills each paper into a reusable gene |
  | 4 | `idea-hybridizer` (opus, ×N) | `ideas_raw_*.json` | recombines genes → 50–100 candidates |
  | 5 | `idea-prioritizer` (opus) | `ideas.json` | scores, dedups, ranks |

- **Multi-source retrieval** (`auto_research_idea/sources/`) — pluggable
  `PaperSource` backends (arXiv, OpenAlex, Semantic Scholar, GitHub awesome-lists,
  venue pages, PDF extraction) fanned out in parallel with backoff; a registry
  merges results by normalized title, enriching each paper from every source that
  found it. Sources **fail soft** — a dead backend returns `[]`, never crashes the run.
- **Dashboard** (`dashboard.py`, stdlib only) — read-only; polls the run artifacts
  and renders variants, papers, genes, and the ranked ideas live. It never runs
  the pipeline, so it can't corrupt a run.

The whole thing coordinates through one contract: **JSON files in `runs/<id>/`.**
Orchestrator, subagents, and dashboard share nothing else — which is what makes
runs reproducible and every stage independently hackable.

---

## ⚙️ Configuration

`config.yaml` tunes the **tools**: paper sources, retrieval limits
(`retrieval.max_papers` ≈ 50), and the digest model/effort. Orchestration knobs
(10 variants, number of parallel hybridizers → 50–100 ideas) live in the skill and
agent files.

---

## 🛠️ Running the Python tools directly (debugging)

The retrieval path needs no key — exercise it standalone:

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
(must never raise — return `[]` on failure). Re-prompt a step by editing its
`.claude/agents/*.md`. Change the run-dir contract in `runstate.py` (the dashboard
reads the same shapes).

---

## ⚠️ Honest limitations

- **Idea quality is bounded by retrieval.** A thin or off-target paper set yields
  thin ideas — tune queries / `max_papers` for unfamiliar areas.
- **Scores are LLM judgments, not ground truth.** Treat novelty/feasibility/impact
  as a triage signal to skim 100 ideas down to 10, not as a verdict.
- **Novelty ≠ correctness.** A high-scoring idea can still be subtly known or
  flawed; the provenance is there so *you* can verify, fast. This is a tool for
  augmenting researcher ideation, not replacing the literature review.

---

## 📝 Notes for hackers

- **Python 3.8 + `anthropic` 0.72.0**: the code deliberately avoids newer SDK
  features (`messages.parse`, `int | None` annotations). See `CLAUDE.md` for the
  full constraints before you refactor.
- **No test framework**: verify by importing the modules and running the real
  (keyless) tools — `CLAUDE.md` → *Verifying changes*.

---

<p align="center"><i>Novelty is recombination at scale. This just does the bookkeeping.</i></p>
