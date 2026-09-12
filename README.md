# Fitness Content Agent

A multi-agent research and scriptwriting pipeline for a running + strength-training ("hybrid athlete") YouTube channel — built on the [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk).

Every week it researches what's actually trending in running/strength/hybrid racing, ranks candidate topics, writes one weekly video script and seven daily reel scripts, and independently fact-checks every claim before anything is allowed to go final.

## How it works

```
python run.py weekly
  │
  ├─ Stage A — trend scouts run in parallel, each in its own isolated context:
  │    events-news · discourse · science-gear · competitor
  │    (+ x-trends, only when the X feature flag is on — see below)
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

## X (Twitter) trending signal — optional, off by default

WebSearch ranks by authority, not by what's spiking today, so "trending" in the default setup is a best-effort
guess. The X integration replaces that guess with measured spike data. It's behind a feature flag so the pipeline
has no dependency on it and costs nothing when off.

```bash
# .env
X_TRENDS_ENABLED=true
X_BEARER_TOKEN=<app-only bearer from the X Developer Console>
```
```bash
python run.py weekly --x      # force on for one run, ignoring .env
python run.py weekly --no-x   # force off for one run (zero billable X calls)
python run.py doctor          # reports whether X is reachable, or that it's disabled
```

**Off** — the original four web-based beats, exactly as before. **On** — adds a fifth `x-trends` scout with two
tools (`get_x_trends`, `search_x_posts`) writing `research/<week>/scout-x-trends.md`, and the ranker is told to use
that as its `trend_velocity` evidence.

Three things worth knowing:

- **Trending ≠ true.** The x-trends scout's job is discovery, not verification. A finding's `claim` describes what
  is *spiking*, and any factual assertion inside it must be re-sourced to a real outlet or flagged UNVERIFIED. The
  deep-researcher is forbidden from carrying a social URL into the claim-source map, and the fact-checker rejects
  any Tier-1 claim whose only citation is a social/forum post. A trend tells you what to look into, nothing more.
- **Spending is capped in code, not by asking the model nicely.** X bills trends per request ($0.010) and search
  **per post returned** ($0.005 each — a 100-result search is $0.50). `X_MAX_SPEND_USD` and `X_MAX_API_CALLS` are
  checked before every call, worst-case-first, and the tool returns a structured "stop calling" error at the
  ceiling. Typical weekly usage is a few cents. Author identities are deliberately not returned.
- **Credentials.** A static app-only Bearer token — no OAuth consent, no refresh, so it works from cron. The app
  must be attached to a Project in the X console or v2 calls 403, and there's no free tier, so the balance needs
  funding. `X_TRENDS_WOEIDS` defaults to `1,23424848` (Worldwide, India); the India WOEID isn't in X's published
  table, so if it 404s, drop it and keep `1`.

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
  x_tools.py                   the in-process `x-trends` MCP server (X REST v2 + spend caps), flag-gated
  hooks.py                     scope_guard, scout_validate, final_gate — the enforcement layer
  pipeline.py                  stage orchestration (the fixed pipeline itself)
  store.py                     parsing/validation for the markdown file formats
  cli.py                       the `weekly / check / finalize / daily / doctor` commands
```

`research/` and `content/` are gitignored — they're generated output, regenerated each week, not project code.

## Current scope / roadmap

**Phase 1 (this repo, on-demand)** — four web-based beats via WebSearch/WebFetch, plus the optional X trends beat
above. Without X, "trending" is a best-effort search-engine signal, strongest in the discourse (forum) beat.

**Phase 2 (planned)** — Reddit API for deeper community-discourse signal (free for non-commercial use, but new
OAuth clients need manual approval, ~1-4 weeks), and YouTube Data API for real competitor view velocity instead of
search snippets.

**Phase 3 (planned)** — scheduled runs (`weekly --auto --stop-at draft` on a timer) instead of on-demand.

## Notes

- No agent ever invents your personal training data — every script has explicit `[YUGANSH: ...]` prompts for you to fill in by hand.
- The `finalize` gate is enforced by a `PreToolUse` hook checking file mtimes, not by asking the model nicely — a stale or missing fact-check report physically blocks the write.
- Each stage has a hard `max_budget_usd` ceiling in `.env` as a safety net.
