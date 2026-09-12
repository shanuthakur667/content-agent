"""X (Twitter) trend tools, gated behind X_TRENDS_ENABLED.

Deliberately a thin two-tool surface over X's REST v2 rather than X's hosted MCP server:
a static app-only Bearer token needs no OAuth consent (so cron works), and the hosted
server exposes 200+ auto-generated tools with no documented way to filter out the
posting tools.

X bills trends per request and search PER POST RETURNED, so spending is capped in code
here — the agent cannot exceed it by deciding to.
"""
from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from claude_agent_sdk import create_sdk_mcp_server, tool

from . import store
from .config import (
    X_API_BASE,
    X_BEARER_TOKEN,
    X_MAX_API_CALLS,
    X_MAX_SPEND_USD,
    X_SEARCH_MAX_RESULTS,
    X_TRENDS_WOEIDS,
)

X_SERVER_NAME = "x-trends"
X_TOOLS = [
    f"mcp__{X_SERVER_NAME}__get_x_trends",
    f"mcp__{X_SERVER_NAME}__search_x_posts",
]

TRENDS_COST_USD = 0.010
POST_READ_COST_USD = 0.005
_TIMEOUT_S = 20


@dataclass
class _Ledger:
    calls: int = 0
    spend_usd: float = 0.0


_ledger = _Ledger()


def ledger_summary() -> str:
    return f"{_ledger.calls} X API call(s), ~${_ledger.spend_usd:.3f} estimated"


def _ok(payload: dict) -> dict:
    body = {"isError": False, "spentSoFarUsd": round(_ledger.spend_usd, 3), **payload}
    return {"content": [{"type": "text", "text": store.dump_json(body)}]}


def _err(category: str, description: str, retryable: bool) -> dict:
    body = {
        "isError": True,
        "errorCategory": category,
        "isRetryable": retryable,
        "description": description,
        "spentSoFarUsd": round(_ledger.spend_usd, 3),
    }
    return {"content": [{"type": "text", "text": store.dump_json(body)}], "is_error": True}


def _reserve(worst_case_usd: float) -> dict | None:
    """Refuse the call up front if it could breach either ceiling."""
    if not X_BEARER_TOKEN:
        return _err("permission", "X_BEARER_TOKEN is not set — the X signal is unavailable this run.", False)
    if _ledger.calls >= X_MAX_API_CALLS:
        return _err(
            "business",
            f"per-run X call cap reached ({X_MAX_API_CALLS} calls). Stop calling X tools and write up "
            f"what you already have. Raise X_MAX_API_CALLS in .env if you need more.",
            False,
        )
    if _ledger.spend_usd + worst_case_usd > X_MAX_SPEND_USD:
        return _err(
            "business",
            f"per-run X spend cap would be exceeded (${_ledger.spend_usd:.3f} spent, this call could cost up to "
            f"${worst_case_usd:.3f}, cap ${X_MAX_SPEND_USD:.2f}). Stop calling X tools and write up what you have.",
            False,
        )
    return None


def _fetch(path: str, params: dict) -> tuple[int, dict, dict]:
    url = f"{X_API_BASE}/{path.lstrip('/')}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {X_BEARER_TOKEN}",
            "User-Agent": "fitness-content-agent/0.1",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:  # noqa: S310 - fixed https base
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}"), dict(resp.headers)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            body = json.loads(raw or "{}")
        except json.JSONDecodeError:
            body = {"raw": raw[:400]}
        return e.code, body, dict(e.headers or {})


def _detail(body: dict) -> str:
    for key in ("detail", "title", "message", "raw"):
        if isinstance(body.get(key), str):
            return body[key][:300]
    errors = body.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        return str(errors[0].get("message") or errors[0])[:300]
    return json.dumps(body)[:300]


def _http_error(status: int, body: dict, headers: dict) -> dict:
    detail = _detail(body)
    if status in (401,):
        return _err("permission", f"X rejected the token (401): {detail}. Check X_BEARER_TOKEN.", False)
    if status in (402, 403):
        return _err(
            "permission",
            f"X denied access ({status}): {detail}. Usual causes: the app is not attached to a Project, the "
            f"endpoint is outside your access level, or the credit balance is unfunded.",
            False,
        )
    if status == 404:
        return _err("validation", f"X returned 404: {detail}. If this was a trends call, the WOEID is probably "
                                  f"not valid — try WOEID 1 (Worldwide).", True)
    if status == 429:
        reset = headers.get("x-rate-limit-reset") or headers.get("X-Rate-Limit-Reset")
        when = ""
        if reset and str(reset).isdigit():
            when = f" Resets at {datetime.fromtimestamp(int(reset), tz=timezone.utc).isoformat()}."
        return _err("transient", f"X rate limit hit (429).{when} Stop calling for now.", True)
    if status >= 500:
        return _err("transient", f"X server error ({status}): {detail}", True)
    return _err("validation", f"X returned {status}: {detail}", True)


@tool(
    name="get_x_trends",
    description=(
        "Returns what is currently trending on X (Twitter) for a location, as trend_name plus tweet_count. "
        "This is real spike data — use it to judge what is genuinely hot right now, which a web search cannot tell you. "
        "Input: woeid (integer location id; omit to sweep the configured defaults — 1 is Worldwide), "
        "max_trends (1-50, default 20). Most results will be politics/entertainment and irrelevant to this channel; "
        "filter for running/endurance/strength/gear/athlete topics yourself. "
        "Costs $0.010 per call against a per-run cap. A trend is evidence of ATTENTION, never evidence that the "
        "underlying claim is true — verify separately. Returns resultCount 0 when a location has no trends, which is "
        "a valid answer, not an error."
    ),
    input_schema={"woeid": int, "max_trends": int},
)
async def get_x_trends(args: dict) -> dict:
    blocked = _reserve(TRENDS_COST_USD)
    if blocked:
        return blocked

    raw_woeid = args.get("woeid")
    woeids = [int(raw_woeid)] if raw_woeid else list(X_TRENDS_WOEIDS)
    max_trends = max(1, min(int(args.get("max_trends") or 20), 50))

    collected: list[dict] = []
    errors: list[str] = []
    for woeid in woeids:
        blocked = _reserve(TRENDS_COST_USD)
        if blocked:
            errors.append("stopped early: per-run cap reached")
            break
        try:
            status, body, headers = await asyncio.to_thread(
                _fetch,
                f"trends/by/woeid/{woeid}",
                {"max_trends": max_trends, "trend.fields": "trend_name,tweet_count"},
            )
        except urllib.error.URLError as e:
            errors.append(f"woeid {woeid}: network error {e.reason}")
            continue
        except Exception as e:  # noqa: BLE001
            errors.append(f"woeid {woeid}: {e}")
            continue

        _ledger.calls += 1
        _ledger.spend_usd += TRENDS_COST_USD

        if status != 200:
            errors.append(f"woeid {woeid}: {_detail(body)[:160]}")
            if status in (401, 402, 403, 429):
                return _http_error(status, body, headers)
            continue

        for item in body.get("data") or []:
            collected.append({
                "woeid": woeid,
                "trend_name": item.get("trend_name") or item.get("name"),
                "post_count": item.get("tweet_count") or item.get("post_count"),
            })

    if not collected and errors:
        return _err("transient", "no trends retrieved: " + "; ".join(errors[:3]), True)
    return _ok({
        "resultCount": len(collected),
        "woeidsQueried": woeids,
        "trends": collected,
        "notes": errors[:3],
    })


@tool(
    name="search_x_posts",
    description=(
        "Searches X (Twitter) posts from the last 7 days and returns their text plus engagement metrics "
        "(like/repost/reply/quote counts) — use the metrics to tell what is actually spiking versus merely existing. "
        "Input: query (X search syntax, e.g. '(HYROX OR marathon) lang:en -is:retweet'), max_results "
        f"(10-{X_SEARCH_MAX_RESULTS}, default 10), sort_order ('relevancy' or 'recency'), hours_back (default 168). "
        "BILLED PER POST RETURNED ($0.005 each) against a per-run dollar cap, so keep max_results small and make each "
        "query count. Author names are deliberately not returned — do not attribute posts to individuals. "
        "resultCount 0 means the query ran and matched nothing, which is a valid answer, not an error."
    ),
    input_schema={"query": str, "max_results": int, "sort_order": str, "hours_back": int},
)
async def search_x_posts(args: dict) -> dict:
    query = (args.get("query") or "").strip()
    if not query:
        return _err("validation", "query is required (X search syntax).", True)

    max_results = max(10, min(int(args.get("max_results") or 10), X_SEARCH_MAX_RESULTS))
    blocked = _reserve(POST_READ_COST_USD * max_results)
    if blocked:
        return blocked

    sort_order = args.get("sort_order") if args.get("sort_order") in ("relevancy", "recency") else "relevancy"
    hours_back = max(1, min(int(args.get("hours_back") or 168), 168))
    start_time = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).replace(microsecond=0)

    params = {
        "query": query,
        "max_results": max_results,
        "sort_order": sort_order,
        "start_time": start_time.isoformat().replace("+00:00", "Z"),
        "tweet.fields": "created_at,public_metrics,lang",
    }
    try:
        status, body, headers = await asyncio.to_thread(_fetch, "tweets/search/recent", params)
    except urllib.error.URLError as e:
        return _err("transient", f"network error reaching X: {e.reason}", True)
    except Exception as e:  # noqa: BLE001
        return _err("transient", f"X search failed: {e}", True)

    posts_raw = body.get("data") or [] if status == 200 else []
    _ledger.calls += 1
    _ledger.spend_usd += POST_READ_COST_USD * len(posts_raw)

    if status != 200:
        return _http_error(status, body, headers)

    posts = []
    for p in posts_raw:
        m = p.get("public_metrics") or {}
        posts.append({
            "id": p.get("id"),
            "url": f"https://x.com/i/status/{p.get('id')}",
            "created_at": p.get("created_at"),
            "text": p.get("text"),
            "likes": m.get("like_count"),
            "reposts": m.get("retweet_count", m.get("repost_count")),
            "replies": m.get("reply_count"),
            "quotes": m.get("quote_count"),
            "impressions": m.get("impression_count"),
        })
    posts.sort(key=lambda x: (x["likes"] or 0) + (x["reposts"] or 0), reverse=True)
    return _ok({
        "resultCount": len(posts),
        "query": query,
        "sortOrder": sort_order,
        "windowHours": hours_back,
        "posts": posts,
    })


def x_trends_server():
    return create_sdk_mcp_server(name=X_SERVER_NAME, version="0.1.0", tools=[get_x_trends, search_x_posts])


async def probe() -> tuple[bool, str]:
    """One cheap trends call, for `run.py doctor`. Goes through the same ledger as the real
    tools, so doctor's reported cost matches what X actually billed."""
    if not X_BEARER_TOKEN:
        return False, "X_BEARER_TOKEN is not set"
    woeid = X_TRENDS_WOEIDS[0] if X_TRENDS_WOEIDS else 1
    try:
        status, body, headers = await asyncio.to_thread(
            _fetch, f"trends/by/woeid/{woeid}", {"max_trends": 5, "trend.fields": "trend_name,tweet_count"}
        )
    except Exception as e:  # noqa: BLE001
        return False, f"network error: {e}"
    _ledger.calls += 1
    _ledger.spend_usd += TRENDS_COST_USD
    if status != 200:
        return False, f"HTTP {status}: {_detail(body)[:200]}"
    names = [t.get("trend_name") or t.get("name") for t in (body.get("data") or [])][:3]
    return True, f"woeid {woeid} returned {len(body.get('data') or [])} trends, e.g. {names}"
