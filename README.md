# Fitness Content Agent

A multi-agent research and scriptwriting pipeline for a running + strength-training ("hybrid athlete") YouTube channel — built on the [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk).

Every week it researches what's actually trending in running/strength/hybrid racing, ranks candidate topics, writes one weekly video script and seven daily reel scripts, and independently fact-checks every claim before anything is allowed to go final.

## How it works

```
python run.py weekly
  │
  ├─ Stage A — 4 trend scouts run in parallel, each in its own isolated context:
  │    events-news · discourse · science-gear · competitor
  │    → research/<week>/scout-<beat>.md  (claim + source url + date, validated by a hook)
  │
  ├─ Stage B — one "editor" session:
  │    1. reads all 4 scout files + a channel-memory MCP server (recent topics,
  │       upcoming races, evergreen backlog) → research/<week>/candidates.md
  │    2. shows you the top candidates — YOU PICK ONE HERE
  │    3. delegates to a `deep-researcher` subagent for a full claim-source map
  │       → research/<week>/synthesis.md
  │    4. writes content/<week>/DRAFT-video.md + DRAFT-reel-01..07.md
  │
  └─ Stage C — an independent fact-checker (fresh context, didn't write the
       scripts) grades every claim against its source, PASS or FAIL with
       specific fixes. Fails auto-revise up to 2 rounds.

python run.py finalize   → DRAFT-* becomes FINAL-* only if a PASS report exists
                            and is newer than the draft. Enforced in code —
                            not something the agent can be asked to skip.

python run.py daily       → picks which already-written reel to record today,
                            given the day of week and upcoming events.

python run.py doctor      → cheap sanity check: is auth working, does
                            WebSearch work through your configured endpoint.
```

Every script cites its sources inline and in a Claims register; every personal
story is a `[YUGANSH: ...]` placeholder the agent never fills in itself — see
`CLAUDE.md` for the full house rules (voice, Hinglish ratio, risk rules).

## Setup

Requires Python 3.10+.

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
cp .env.example .env   # then fill in ONE auth option — see comments in the file
.venv/bin/python run.py doctor
```

`.env` also holds per-stage model overrides and hard USD budget caps (see
`.env.example`) — everything defaults to Sonnet for now to keep costs down;
swap the fact-checker to Opus first if you want higher-precision review.

## Usage

```bash
python run.py weekly --stop-at draft   # scouts → rank (you pick) → research → drafts
python run.py check                    # fact-check existing drafts, auto-revise on FAIL
python run.py finalize                 # DRAFT-* → FINAL-* for anything with a fresh PASS
python run.py daily                    # pick today's reel from the week's bank
python run.py weekly --auto            # full run, no prompts, top-ranked topic
```

## Project layout

```
CLAUDE.md                    persona, audience, Hinglish ratio, hard script rules
channel/                      voice.md, risk-rules.md, event-calendar.md, evergreen-backlog.md
templates/                    the exact format each agent must fill in
research/<week>/              scout findings, ranked candidates, synthesis, fact-check reports
content/<week>/                DRAFT-*.md and FINAL-*.md scripts
content/calendar.jsonl         append-only log of everything ever produced (source of truth for "already covered")
src/fitness_agent/
  config.py                    paths, week id, models, budgets (from .env)
  prompts.py                   every agent's system prompt and per-task instructions
  agents.py                    ClaudeAgentOptions + AgentDefinition for each agent role
  tools.py                     the in-process `channel-memory` MCP server
  hooks.py                     scope_guard, scout_validate, final_gate — the enforcement layer
  pipeline.py                  stage orchestration (the fixed pipeline itself)
  store.py                     parsing/validation for the markdown file formats
  cli.py                       the `weekly / check / finalize / daily / doctor` commands
```

`research/` and `content/` are gitignored — they're generated output, regenerated each week, not project code.

## Current scope / roadmap

**Phase 1 (this repo, on-demand)** — WebSearch/WebFetch only for trend detection; no Reddit/YouTube API yet, so "trending" is a best-effort search-engine signal, strongest in the discourse (forum) beat.

**Phase 2 (planned)** — real Reddit API and YouTube Data API MCP servers for an actual trend/velocity signal instead of generic web search; requires Node and per-service API approval.

**Phase 3 (planned)** — scheduled runs (`weekly --auto --stop-at draft` on a timer) instead of on-demand.

## Notes

- No agent ever invents your personal training data — every script has explicit `[YUGANSH: ...]` prompts for you to fill in by hand.
- The `finalize` gate is enforced by a `PreToolUse` hook checking file mtimes, not by asking the model nicely — a stale or missing fact-check report physically blocks the write.
- Each stage has a hard `max_budget_usd` ceiling in `.env` as a safety net.
