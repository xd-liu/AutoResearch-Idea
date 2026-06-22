# Improvement Log

A lightweight, append-only development journal maintained by the
`self-improvement` subagent. It captures — concisely — what to learn from each
working session so the system can be improved over time. **Not** part of the
research-idea pipeline.

Each entry is terse on purpose (bullets, paraphrased, no transcripts). Sections:
- **Prompts** — what the user asked for, one line each (paraphrased).
- **Insights** — design/architecture learnings worth keeping.
- **Issues & fixes** — problems hit and how they were resolved.

---

## 2026-06-22 — Session 1: initial build (CLI → skills → subagents + dashboard)

**Prompts**
- Build a research-idea system: meta-idea → brainstorm → gather papers → hybridize (杂交衍生) → ranked paper ideas.
- Why build with Python code instead of skills?
- Want skills as the orchestrator + Python for search/dedup/parallel analysis.
- Add a web UI (input + live per-step view); scale to 10 variants / ~50 papers / 50–100 ideas; one skill per step.
- Web runtime: Claude Code orchestrates, webpage is read-only dashboard.
- Each step → its own subagent; retrieve + digest on Sonnet to save cost.
- Only have an Anthropic subscription — how to use the API?
- Add GitHub "awesome" lists as a paper source.
- Add a separate `self-improvement` logging agent.

**Insights**
- Boundary that worked: subagents/skills do creative + judgment + orchestration; Python tools do deterministic, parallel, mechanical work (retrieval, dedup, ranking).
- Subscription ≠ API credits. Run on the subscription via Claude Code subagents; digest **in-context** (Sonnet) rather than the Python API tool, so no `ANTHROPIC_API_KEY` is needed for the default flow.
- GitHub awesome-lists are high-signal curated sources; extracting the **bold** title makes entries dedup/enrich against arXiv/OpenAlex (e.g. picked up 9k citations via title merge).
- Env ceiling: Python 3.8 + `anthropic` 0.72.0 (no `messages.parse` / `output_config` / adaptive-thinking params) → use prompt-driven JSON + Pydantic validation; keep Pydantic field types 3.8-safe.
- Contract between orchestrator and dashboard is a `runs/<id>/` dir of JSON artifacts + `status.json`; keep artifact filenames identical across writer/skill/dashboard.

**Issues & fixes**
- Paper APIs rate-limit hard → backoff with `Retry-After`; fan out across sources but query each source serially. (S2 still 429s unauthenticated — a key helps.)
- Secrets put in `.env.example` (committed template; also `dotenv` loads `.env`, not `.env.example`) → real keys must go in `.env`.
- Review-found correctness: `_merge` dropped the losing record's fields → merge field-by-field (longest abstract, non-empty year/venue, max citations).
- `Retry-After` as an HTTP-date crashed `float()` → parse defensively, floor at backoff.
- Dashboard 500'd on malformed LLM artifacts (non-dict list items) → filter to dicts + handler-level try/except; non-atomic artifact writes → tmp+replace; `pending` mis-ordered same-second runs → sort by mtime; symlink could escape `runs/` → resolve + containment check.
- Brainstorm count / hybridize variant-split were hard-coded → verify count, split into N roughly-equal groups; prioritizer guards against too-few pooled ideas.
