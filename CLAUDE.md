# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A research-idea generation system: a seed meta-idea → 10 brainstorm variants →
~100 retrieved papers → digested "genes" → 50–100 cross-bred (杂交衍生) ideas →
scored & ranked → adversarially reviewed (with per-step credit assignment). It is
**skill-orchestrated**: Claude Code runs the `research-ideas` skill, which
delegates each step to a subagent; a live web dashboard visualizes progress and
lets a human annotate ideas (notes / score / rank). See `README.md` for the
user-facing walkthrough.

## Two constraints that will bite you if ignored

1. **Environment ceiling: Python 3.8 + `anthropic` 0.72.0.** Newer SDKs need
   Python ≥3.9, and only 3.8 is available here. Consequences:
   - The 0.72.0 SDK has **no `messages.parse`**, no `output_config`, no
     adaptive-thinking params. `llm.py` deliberately uses prompt-driven JSON +
     Pydantic validation (`_extract_json` + a repair retry) and streamed
     `messages.create`. Do **not** "modernize" it to `messages.parse`.
   - Pydantic evaluates field annotations at runtime, so **`models.py` must use
     `Optional[...]` / `List[...]`, never `int | None` / `list[str]`** (those
     `TypeError` on 3.8). Other modules use `from __future__ import annotations`,
     so their annotations are safe as strings.
   - Always invoke with the venv interpreter: `.venv/bin/python` (system
     `python3` is also 3.8 but lacks the deps).

2. **Subscription, not API credits.** The default pipeline runs on a Claude
   Code Pro/Max **subscription** — the subagents (including `paper-digester`,
   which digests *in-context* on Sonnet) need no `ANTHROPIC_API_KEY`. The only
   thing that needs API credits is the optional standalone `digest.py` tool.
   `search` and the dashboard need no key at all. Secrets live in `.env` (loaded
   by `python-dotenv`), never `.env.example`.

## Architecture (the big picture)

**Skill/subagent = brain; Python = hands.** Creative/judgment/orchestration work
is done by Claude Code subagents; deterministic, parallel, mechanical work
(multi-source retrieval, dedup, ranking) is Python tools. When adding a step,
decide which side it belongs on.

The pipeline, orchestrated by `.claude/skills/research-ideas/SKILL.md`:

```
idea-brainstormer(opus) → paper-retriever(sonnet) → paper-digester(sonnet)
   → idea-hybridizer(opus, ×N parallel) → idea-prioritizer(opus)
   → idea-critic(opus, ×N parallel)
```

Each step is a subagent in `.claude/agents/*.md` — the unit you optimize
independently (model is set per-agent in its frontmatter; cheap mechanical steps
use Sonnet, creative steps use Opus). `self-improvement.md` is a separate
meta-agent (NOT in the pipeline) that appends to `IMPROVEMENT_LOG.md`.

**The run-directory contract is the spine.** The orchestrator, every subagent,
and the dashboard coordinate *only* through JSON files in `runs/<id>/`. This is
the invariant that spans the most files — keep it consistent when changing
anything:

| File | Written by | Read by |
|---|---|---|
| `status.json` | orchestrator via `runstate.py` (atomic) | dashboard (polls), `runstate pending` |
| `brainstorm.json` | idea-brainstormer | retriever (queries), hybridizer, dashboard |
| `papers.json` | search tool (atomic) | digester, dashboard |
| `genes.json` / `genes_<k>.json` | digester | hybridizer, dashboard |
| `ideas_raw_<k>.json` | each parallel hybridizer (distinct filename!) | prioritizer, dashboard |
| `ideas.json` | idea-prioritizer | dashboard, critic, final report |
| `reviews_<k>.json` | each parallel idea-critic (distinct filename!) | `credit.py`, dashboard |
| `credit_summary.json` | `credit.py` (aggregates reviews) | dashboard, final report |
| `annotations.json` | dashboard (human notes/score/rank; non-destructive) | dashboard |

`runstate.py` defines `STEPS` and the `status.json` schema; `dashboard.py`
`_gather_run` reads these shapes. Artifacts are LLM-written, so readers must
tolerate malformed shapes (dashboard filters to dicts and guards every handler).
Filenames and JSON field names must match across the writer agent, `SKILL.md`,
and `dashboard.py` — a rename in one place silently blanks the dashboard.

**Paper sources** (`auto_research_idea/sources/`) implement `PaperSource.search`
and must **never raise** (return `[]` on failure — `registry.search_all` fans out
across sources in parallel but queries each source serially to respect rate
limits; `_http.get_with_retry` does backoff). `registry._merge` dedups by
normalized **title** and combines fields (longest abstract, max citations), so a
paper found by several sources gets enriched. The `github` source mines
`awesome-<topic>` READMEs and emits `arxiv:<id>` source_ids + bold-extracted
titles specifically so they merge/enrich with the arXiv/OpenAlex results.

**Top-venue coverage = a registry + per-family drivers** (no single API spans
all venues). Each retrieved paper stores `landing_url` (official page) + `pdf_url`:
- `venue_pages` — CV venues (CVPR/ICCV/WACV): the thecvf *virtual* site for
  abstracts + the CVF *openaccess* index (`openaccess.thecvf.com/<V><Y>?day=all`)
  for a title→PDF map. (openaccess 406s on an `Accept: text/html` header — fetch
  it with only a User-Agent.) Registry-driven via `config.yaml venue_pages.registry`.
- `openreview` — ML venues (ICLR/NeurIPS/ICML/CoRL): queries accepted papers by
  `content.venueid`, returning title+abstract+direct PDF (covers e.g. ICLR 2026,
  which arXiv/OpenAlex miss). Config: `openreview.venues`.
- `acl_anthology` — NLP venues (ACL/EMNLP/NAACL + Findings): parses each event
  page (`aclanthology.org/events/<venue>-<year>/`) for title + inline abstract +
  direct PDF. Note: hrefs are unquoted; titles are split by `acl-fixed-case`
  spans (drop those spans without inserting a space).
- `ecva` — ECCV (open-access PDFs on `ecva.net/papers.php`). ECCV is biennial, so
  it keeps editions back to `retrieval.ecva_years` (don't filter to current_year-1,
  which is never an ECCV year). Abstracts are backfilled by enrichment.
- IROS/ICRA (robotics) are IEEE-Xplore-paywalled — no open-access PDF driver is
  possible; they come through OpenAlex/Semantic-Scholar (metadata/abstracts) and
  arXiv preprints (PDF when one exists).
- Ranking (`registry._rank`/`_score`) weights relevance > recency > top-venue >
  citations; `min_year` + heavy recency weight favor the current year. PDF
  intro/conclusion extraction (`pdf_extract.py`) is gated by `retrieval.parse_pdf`.

## Commands

```bash
# Setup
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # real keys go ONLY in .env

# Run the pipeline: talk to Claude Code in this dir, e.g.
#   "use the research-ideas skill on: <meta-idea>"
# (it spawns the subagents; there is no single CLI command for the full run)

# Live dashboard (separate terminal)
.venv/bin/python -m auto_research_idea.dashboard            # http://localhost:8000

# Run the Python tools directly (debugging)
.venv/bin/python -m auto_research_idea.search --queries "graph neural network CO" --out papers.json
.venv/bin/python -m auto_research_idea.search --queries-file queries.json --out papers.json
.venv/bin/python -m auto_research_idea.digest --papers papers.json --out genes.json   # needs API credits

# Run-state helper (used by the orchestrator; also handy for inspection)
.venv/bin/python -m auto_research_idea.runstate new --meta "<idea>"          # -> prints run dir
.venv/bin/python -m auto_research_idea.runstate pending                       # newest queued run dir
```

Tuning lives in `config.yaml` (sources incl. the `venue_pages` registry,
`retrieval.max_papers` ≈ 100, `retrieval.enrich_abstracts` / `parse_pdf`, the
digest model/effort) and in the skill/agent files (variant count, number of
parallel hybridizers → idea count, number of critics).

## Verifying changes

There is **no test framework** in this repo. Verify by importing the modules and
exercising the real tools (keyless paths work without credits), e.g.:

```bash
.venv/bin/python -c "import auto_research_idea.search, auto_research_idea.dashboard, auto_research_idea.runstate"
.venv/bin/python -m auto_research_idea.search --queries "<topic>" --out /tmp/p.json   # live, no key
```

For dashboard/runstate logic, drive a synthetic `runs/<id>/` with hand-written
artifacts and call `dashboard._gather_run` / `runstate.set_step` directly. Paper
sources can be tested live without keys (arXiv/OpenAlex/GitHub work
unauthenticated; Semantic Scholar 429s without a key — expected, it fails soft).
