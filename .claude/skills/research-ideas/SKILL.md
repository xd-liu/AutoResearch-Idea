---
name: research-ideas
description: >-
  Orchestrate the research-idea pipeline: turn a seed meta-idea into a ranked list
  of 50-100 novel paper ideas. You delegate each step to a dedicated subagent
  (brainstorm → retrieve → digest → hybridize → prioritize), tracking progress in
  a run directory that the live web dashboard reads. Use this whenever the user
  gives a research topic/meta-idea and wants concrete paper ideas, or asks to run
  a queued idea from the dashboard.
---

# Research Idea Generation — Orchestrator

You are the **orchestrator**. You do NOT do the steps yourself — you delegate each
to a dedicated **subagent** (defined in `.claude/agents/`), and you keep the run's
`status.json` updated so the read-only web dashboard shows live progress.

```
brainstorm ─▶ retrieve ─▶ digest ─▶ hybridize (×N parallel) ─▶ prioritize ─▶ review (×N parallel)
 (opus)       (sonnet)    (sonnet)   (opus)                     (opus)         (opus)
```

Subagents (spawn via the Task tool with the matching `subagent_type`):
| step | subagent_type | model | writes |
|------|---------------|-------|--------|
| brainstorm | `idea-brainstormer` | opus | `brainstorm.json` (10 variants + queries) |
| retrieve | `paper-retriever` | sonnet | `papers.json` (~100 papers; runs the search tool) |
| digest | `paper-digester` | sonnet | `genes.json` (runs the digest tool) |
| hybridize | `idea-hybridizer` | opus | `ideas_raw_<k>.json` (run several in parallel) |
| prioritize | `idea-prioritizer` | opus | `ideas.json` (scored & ranked) |
| review | `idea-critic` | opus | `reviews_<k>.json` (adversarial review + credit; run several in parallel) |

Run everything from the project root (the dir containing `config.yaml`). Use the
project venv (`.venv/bin/python`, fall back to `python3`).

**Auth:** the subagents run on your Claude Code session — a Pro/Max **subscription
is fine, no `ANTHROPIC_API_KEY` needed**. The `search` tool is keyless HTTP, and
the digest step runs **in-context on Sonnet**. (Only the optional faster Python
`digest` tool needs API credits.)

## 0. Resolve the run

- If the user gave a meta-idea, create a run:
  ```bash
  .venv/bin/python -m auto_research_idea.runstate new --meta "<META IDEA>"
  ```
  This prints the **run directory** (e.g. `runs/diffusion-for-...-a1b2c3`). Capture
  it — call it `RUN` below.
- If the user asks to run a **queued** idea from the dashboard (or gave no idea),
  find it:
  ```bash
  .venv/bin/python -m auto_research_idea.runstate pending
  ```
  If it prints a dir, use that as `RUN` (read its `request.json` for the meta-idea).
  If empty, ask the user for a meta-idea.
- Tell the user the dashboard URL (`http://localhost:8000`) and, if it may not be
  running, how to start it: `.venv/bin/python -m auto_research_idea.dashboard`.

Mark a step `running` BEFORE spawning its subagent and `done` after it returns,
so the dashboard reflects progress live:
```bash
.venv/bin/python -m auto_research_idea.runstate set --run-dir "$RUN" --step <step> --state running
.venv/bin/python -m auto_research_idea.runstate set --run-dir "$RUN" --step <step> --state done --summary "<subagent's summary>" --artifact <file>
```
If a step fails, set its state to `error` and stop, telling the user what broke.

## 1. Brainstorm
Set `brainstorm` running. Spawn `idea-brainstormer` with a prompt like:
> Meta-idea: "<META IDEA>". Run directory: `<RUN>`. Produce 10 distinct variants
> and the search queries, and write `<RUN>/brainstorm.json` per your instructions.

On return, read `brainstorm.json` and sanity-check it has ~10 variants and ≥6
queries; if it's far off, re-spawn the brainstormer before continuing. Then set
`brainstorm` done (summary = its reply, artifact = `brainstorm.json`).

## 2. Retrieve
Set `retrieve` running. Spawn `paper-retriever` with: `Run directory: <RUN>. Read
the queries from brainstorm.json and run the search tool to write <RUN>/papers.json.`
The sources (config.yaml) include arXiv, Semantic Scholar, OpenAlex, and GitHub
awesome-lists. On return, set `retrieve` done (artifact = `papers.json`). If far
fewer than ~50 papers came back, you may spawn it again after asking the
brainstormer for more queries.

## 3. Digest
Set `digest` running. Spawn `paper-digester` with: `Run directory: <RUN>. Read
papers.json and digest each paper that has an abstract into a gene, writing
<RUN>/genes.json.` It digests **in-context on Sonnet** (no API key). For a large
corpus you may split papers.json across 2-3 `paper-digester` subagents in parallel,
each writing its own `genes_<k>.json`. On return, set `digest` done (artifact =
`genes.json`). _(If you have API credits and want speed instead, run the parallel
tool: `.venv/bin/python -m auto_research_idea.digest --papers <RUN>/papers.json --out <RUN>/genes.json`.)_

## 4. Hybridize (parallel)
Set `hybridize` running. To reach **50-100 ideas**, spawn **4-5 `idea-hybridizer`
subagents IN PARALLEL** (multiple Task calls in one message). Split the variants
(however many `brainstorm.json` actually has) into 3-4 roughly equal groups and
give each hybridizer one group plus a distinct lens — and give **each a distinct
output filename** `ideas_raw_<k>.json` (k = 1, 2, 3, …) so they never overwrite
each other. Example lenses:
- most-cited genes + variant group 1 → `ideas_raw_1.json`
- genes with the richest `transferable_concepts` + variant group 2 → `ideas_raw_2.json`
- variant group 3 → `ideas_raw_3.json`
- cross-source combinations that attack `limitations` in the gene library → `ideas_raw_4.json`

Tell each to produce ~15-20 ideas so the union is 50-100. On return, verify the
`ideas_raw_*.json` files are distinct and non-empty, then set `hybridize` done
(summary = total idea count, artifact = `ideas_raw_*.json`).

## 5. Prioritize
Set `prioritize` running. Spawn `idea-prioritizer` with: `Run directory: <RUN>.
Pool every ideas_raw*.json, dedupe, score, rank, and write <RUN>/ideas.json.`
On return, set `prioritize` done (artifact = `ideas.json`).

## 6. Review (critic + overlap + credit, parallel)
Set `review` running. Spawn **2-4 `idea-critic` subagents IN PARALLEL**, splitting
the ranked ideas in `ideas.json` into contiguous rank slices. Give each its slice
(e.g. "ranks 1-17") and a **distinct output filename** `reviews_<k>.json`. Each
critic adversarially reviews its ideas — strengths/novelty/impact, defects, and
overlap with the retrieved papers — and assigns each merit/defect to the pipeline
step responsible. On return, aggregate the per-step credit:
```bash
.venv/bin/python -m auto_research_idea.credit --run-dir "$RUN"   # writes credit_summary.json
```
Then set `review` done (summary = the weakest/strongest step from `credit_summary.json`,
artifact = `reviews_1.json`).

## 7. Wrap up
Read `<RUN>/ideas.json` and `<RUN>/credit_summary.json`. Sanity-check the idea count
is in the expected ~50-100 range; if it's far lower (or zero), the hybridizers likely
collided on a filename or failed — check that each `ideas_raw_*.json` exists and is
distinct, and re-run the affected step before declaring success. Then show the user
the top ~10 ranked titles with scores, call out any `likely-duplicate` verdicts and
the **weakest pipeline step** (from the credit summary, for improving the tool), and
point them at the dashboard (`http://localhost:8000`) for the full live view.

## Notes & tuning
- The dashboard never runs the pipeline — it visualizes `status.json` and the
  artifacts. Its only writes are **queueing** a run (type a meta-idea, pick it up
  via `runstate pending`) and saving human **annotations** (notes / score / rank)
  to `annotations.json` (non-destructive; `ideas.json` is never overwritten).
- Scale knobs: 10 variants (brainstormer), ~100 papers (`config.yaml`
  `retrieval.max_papers`), 50-100 ideas (number of hybridizers × ideas each),
  2-4 critics (review).
- retrieve & digest run on **Sonnet** to save cost; the creative/judgment steps
  run on **Opus**. Change a step's model in its `.claude/agents/*.md` file.
- Keep `source_id`s intact so ideas trace back to real papers in the dashboard.
