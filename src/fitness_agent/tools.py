from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from . import store

SERVER_NAME = "channel-memory"
CHANNEL_MEMORY_TOOLS = [
    f"mcp__{SERVER_NAME}__get_recent_topics",
    f"mcp__{SERVER_NAME}__get_upcoming_events",
    f"mcp__{SERVER_NAME}__get_evergreen_backlog",
]


def _ok(payload: dict) -> dict:
    return {"content": [{"type": "text", "text": store.dump_json({"isError": False, **payload})}]}


def _err(category: str, description: str, retryable: bool) -> dict:
    body = {"isError": True, "errorCategory": category, "isRetryable": retryable, "description": description}
    return {"content": [{"type": "text", "text": store.dump_json(body)}], "is_error": True}


@tool(
    name="get_recent_topics",
    description=(
        "Lists topics this channel has already covered (weekly videos and daily reels) within the last N weeks, "
        "from the content calendar. Call this BEFORE ranking candidates to avoid repeating a topic. "
        "Input: weeks (integer, use 8 unless told otherwise). Returns resultCount and a topics list with week, type, "
        "topic, status. resultCount 0 is a valid answer for a new channel, not an error. "
        "Do NOT use this for upcoming races — use get_upcoming_events."
    ),
    input_schema={"weeks": int},
)
async def get_recent_topics(args: dict[str, Any]) -> dict:
    try:
        weeks = int(args.get("weeks") or 8)
        cutoff = (date.today() - timedelta(weeks=weeks)).isoformat()
        topics = [
            {"week": e.week, "type": e.type, "topic": e.topic, "status": e.status}
            for e in store.calendar_state().values()
            if e.created_at[:10] >= cutoff
        ]
        topics.sort(key=lambda t: t["week"], reverse=True)
        return _ok({"resultCount": len(topics), "weeks": weeks, "topics": topics})
    except Exception as e:  # noqa: BLE001
        return _err("transient", f"could not read content/calendar.jsonl: {e}", True)


@tool(
    name="get_upcoming_events",
    description=(
        "Lists races and competitions from channel/event-calendar.md that start within the next N days (plus anything "
        "that finished in the last 3 days). Search demand for an event peaks in the 1-2 weeks BEFORE it, so use this to "
        "bias topic ranking and reel timing. Input: days (integer, e.g. 30). Each event has date, end_date, event, type, "
        "why_it_matters, days_until, verified. Unverified rows must not be cited in a script without confirming the date. "
        "Do NOT use this to find past results or news — that is the scouts' job."
    ),
    input_schema={"days": int},
)
async def get_upcoming_events(args: dict[str, Any]) -> dict:
    try:
        days = int(args.get("days") or 30)
        today = date.today()
        lo, hi = (today - timedelta(days=3)).isoformat(), (today + timedelta(days=days)).isoformat()
        events = []
        for ev in store.load_events():
            if not ev["date"] or not (lo <= ev["date"] <= hi):
                continue
            ev["days_until"] = (date.fromisoformat(ev["date"]) - today).days
            events.append(ev)
        events.sort(key=lambda e: e["date"])
        return _ok({"resultCount": len(events), "window_days": days, "events": events})
    except FileNotFoundError as e:
        return _err("permission", f"event calendar missing: {e}", False)
    except Exception as e:  # noqa: BLE001
        return _err("transient", f"could not parse channel/event-calendar.md: {e}", True)


@tool(
    name="get_evergreen_backlog",
    description=(
        "Returns the still-open evergreen story ideas from channel/evergreen-backlog.md (legends like the Iron Cowboy, "
        "durable science topics, gear explainers) with a suggested angle and format. Use in a slow news week, or to wrap "
        "an evergreen framework around a trending hook. Every entry is a lead, not a verified fact — numbers must still "
        "be sourced through research. Takes no input."
    ),
    input_schema={},
)
async def get_evergreen_backlog(args: dict[str, Any]) -> dict:
    try:
        items = [
            {"id": i["id"], "topic": i["topic"], "angle": i["angle"], "format": i["format"]}
            for i in store.load_evergreen()
            if i["status"] == "open"
        ]
        return _ok({"resultCount": len(items), "items": items})
    except Exception as e:  # noqa: BLE001
        return _err("transient", f"could not parse channel/evergreen-backlog.md: {e}", True)


def channel_memory_server():
    return create_sdk_mcp_server(
        name=SERVER_NAME,
        version="0.1.0",
        tools=[get_recent_topics, get_upcoming_events, get_evergreen_backlog],
    )
