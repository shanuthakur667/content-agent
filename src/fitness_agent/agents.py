from __future__ import annotations

from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, HookMatcher

from .config import (
    CHECKER_MAX_BUDGET_USD,
    CHECKER_MAX_TURNS,
    CHECKER_MODEL,
    EDITOR_MAX_BUDGET_USD,
    EDITOR_MAX_TURNS,
    EDITOR_MODEL,
    PROJECT_ROOT,
    RESEARCHER_MODEL,
    SCOUT_MAX_BUDGET_USD,
    SCOUT_MAX_TURNS,
    SCOUT_MODEL,
    content_dir,
    research_dir,
)
from .hooks import final_gate, make_scope_guard, report_validate, scout_validate
from .prompts import CHECKER_PROMPT, DAILY_PROMPT, EDITOR_SYSTEM_APPEND, RESEARCHER_PROMPT, scout_system_prompt
from .tools import CHANNEL_MEMORY_TOOLS, SERVER_NAME, channel_memory_server


def _alias(model_id: str) -> str:
    """AgentDefinition takes a family alias, not a full model id."""
    for family in ("opus", "sonnet", "haiku"):
        if family in model_id:
            return family
    return "inherit"


def scout_options(beat: str, week: str) -> ClaudeAgentOptions:
    rd = research_dir(week)
    return ClaudeAgentOptions(
        system_prompt=scout_system_prompt(beat, week),
        allowed_tools=["WebSearch", "WebFetch", "Read", "Write"],
        permission_mode="dontAsk",
        cwd=PROJECT_ROOT,
        model=SCOUT_MODEL,
        max_turns=SCOUT_MAX_TURNS,
        max_budget_usd=SCOUT_MAX_BUDGET_USD,
        hooks={
            "PreToolUse": [HookMatcher(matcher="Write|Edit", hooks=[make_scope_guard([(rd, f"scout-{beat}.md")])])],
            "PostToolUse": [HookMatcher(matcher="Write|Edit", hooks=[scout_validate])],
        },
    )


def editor_options(week: str) -> ClaudeAgentOptions:
    rd, cd = research_dir(week), content_dir(week)
    scope = make_scope_guard([
        (rd, "candidates.md"),
        (rd, "synthesis.md"),
        (cd, "DRAFT-*.md"),
        (cd, "FINAL-*.md"),
    ])
    return ClaudeAgentOptions(
        system_prompt={"type": "preset", "preset": "claude_code", "append": EDITOR_SYSTEM_APPEND},
        setting_sources=["project"],
        allowed_tools=["Read", "Write", "Edit", "Glob", "Grep", "WebSearch", "WebFetch", "Agent", *CHANNEL_MEMORY_TOOLS],
        permission_mode="dontAsk",
        cwd=PROJECT_ROOT,
        model=EDITOR_MODEL,
        max_turns=EDITOR_MAX_TURNS,
        max_budget_usd=EDITOR_MAX_BUDGET_USD,
        mcp_servers={SERVER_NAME: channel_memory_server()},
        agents={
            "deep-researcher": AgentDefinition(
                description=(
                    "Deep research and source synthesis for the chosen weekly topic. Reads the scout files, searches "
                    "further, and writes research/<week>/synthesis.md with a claim-source map. Use BEFORE writing scripts."
                ),
                prompt=RESEARCHER_PROMPT,
                tools=["Read", "Write", "WebSearch", "WebFetch"],
                model=_alias(RESEARCHER_MODEL),
            ),
        },
        hooks={
            "PreToolUse": [HookMatcher(matcher="Write|Edit|Bash", hooks=[scope, final_gate])],
            "PostToolUse": [HookMatcher(matcher="Write|Edit", hooks=[scout_validate])],
        },
    )


def checker_options(week: str) -> ClaudeAgentOptions:
    rd = research_dir(week)
    return ClaudeAgentOptions(
        system_prompt=CHECKER_PROMPT,
        allowed_tools=["Read", "WebSearch", "WebFetch", "Write"],
        permission_mode="dontAsk",
        cwd=PROJECT_ROOT,
        model=CHECKER_MODEL,
        max_turns=CHECKER_MAX_TURNS,
        max_budget_usd=CHECKER_MAX_BUDGET_USD,
        hooks={
            "PreToolUse": [HookMatcher(matcher="Write|Edit", hooks=[make_scope_guard([(rd, "fact-check-*.md")])])],
            "PostToolUse": [HookMatcher(matcher="Write|Edit", hooks=[report_validate])],
        },
    )


def daily_options(week: str, fresh: bool) -> ClaudeAgentOptions:
    tools = ["Read", f"mcp__{SERVER_NAME}__get_upcoming_events"]
    if fresh:
        tools.append("WebSearch")
    return ClaudeAgentOptions(
        system_prompt=DAILY_PROMPT,
        allowed_tools=tools,
        permission_mode="dontAsk",
        cwd=PROJECT_ROOT,
        model=SCOUT_MODEL,
        max_turns=15,
        max_budget_usd=1.0,
        mcp_servers={SERVER_NAME: channel_memory_server()},
    )
