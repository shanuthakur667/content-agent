from __future__ import annotations

import asyncio
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)

from . import store, x_tools
from .agents import checker_options, daily_options, editor_options, scout_options
from .config import (
    CHECKER_CONCURRENCY,
    FACTCHECK_MAX_RETRIES,
    PROJECT_ROOT,
    SCOUT_MODEL,
    X_MAX_API_CALLS,
    X_TRENDS_ENABLED,
    content_dir,
    rel,
    research_dir,
    scout_beats,
)
from .hooks import gate_check, report_path_for
from .prompts import checker_task, daily_pick_task, rank_task, revise_task, scout_task, write_task
from .schemas import CalendarEntry, FactCheckVerdict

_CANDIDATE = re.compile(r"^###\s+(\d+)\.\s+(.+?)\s*$", re.MULTILINE)
_META = re.compile(r"^(slot|idea|topic):\s*(.+?)\s*$", re.MULTILINE)
_SLOTS = ("topic-teaser", "topic-clip", "topic-followup", "news-reactive", "evergreen")


@dataclass
class RunResult:
    label: str
    text: str = ""
    cost_usd: float = 0.0
    turns: int = 0
    is_error: bool = False
    tool_calls: list[str] = field(default_factory=list)


@dataclass
class Candidate:
    rank: int
    title: str
    score: float
    hook: str


def _log(label: str, msg: str) -> None:
    print(f"[{label}] {msg}", flush=True)


def _describe(block: ToolUseBlock) -> str:
    args = block.input or {}
    for key in ("query", "url", "file_path", "pattern", "description", "prompt"):
        if key in args:
            return f"{block.name}: {str(args[key]).replace(chr(10), ' ')[:100]}"
    return block.name


def _handle(msg, label: str, result: RunResult) -> None:
    if isinstance(msg, AssistantMessage):
        for block in msg.content:
            if isinstance(block, ToolUseBlock):
                result.tool_calls.append(block.name)
                _log(label, _describe(block))
            elif isinstance(block, TextBlock) and block.text.strip():
                result.text = block.text
    elif isinstance(msg, ResultMessage):
        result.cost_usd = msg.total_cost_usd or 0.0
        result.turns = msg.num_turns or 0
        result.is_error = bool(msg.is_error)
        if msg.result:
            result.text = msg.result
        state = "ERROR" if result.is_error else "done"
        _log(label, f"{state} — {result.turns} turns, ${result.cost_usd:.2f}")


async def run_query(prompt: str, options, label: str) -> RunResult:
    result = RunResult(label)
    async for msg in query(prompt=prompt, options=options):
        _handle(msg, label, result)
    return result


async def drain(client: ClaudeSDKClient, label: str) -> RunResult:
    result = RunResult(label)
    async for msg in client.receive_response():
        _handle(msg, label, result)
    return result


async def run_scouts(week: str, beats: tuple[str, ...]) -> dict[str, str]:
    rd = research_dir(week)
    results = await asyncio.gather(
        *(run_query(scout_task(beat, week), scout_options(beat, week), beat) for beat in beats),
        return_exceptions=True,
    )
    statuses: dict[str, str] = {}
    for beat, res in zip(beats, results):
        path = rd / f"scout-{beat}.md"
        if isinstance(res, BaseException):
            statuses[beat] = f"crashed: {res}"
        elif not path.exists():
            statuses[beat] = "missing file"
        else:
            report = store.parse_scout_report(path.read_text(encoding="utf-8"))
            extra = f", {len(report.problems)} problems" if report.problems else ""
            statuses[beat] = f"{report.status} ({len(report.findings)} findings{extra})"
    for beat, status in statuses.items():
        _log("scouts", f"{beat}: {status}")
    return statuses


def parse_candidates(text: str) -> list[Candidate]:
    parts = _CANDIDATE.split(text)
    out = []
    for i in range(1, len(parts), 3):
        body = parts[i + 2]
        score = re.search(r"^score:\s*([-+]?\d+(?:\.\d+)?)", body, re.MULTILINE)
        hook = re.search(r"^hook:\s*(.+)$", body, re.MULTILINE)
        out.append(Candidate(
            rank=int(parts[i]),
            title=parts[i + 1].strip(),
            score=float(score.group(1)) if score else 0.0,
            hook=hook.group(1).strip() if hook else "",
        ))
    return out


def choose(candidates: list[Candidate], auto: bool, topic_index: int | None) -> Candidate:
    print("\nCandidates:")
    for c in candidates:
        print(f"  {c.rank}. {c.title}  (score {c.score:g})")
        if c.hook:
            print(f"     hook: {c.hook[:140]}")
    if topic_index:
        return candidates[min(max(topic_index, 1), len(candidates)) - 1]
    if auto:
        return candidates[0]
    while True:
        raw = input("\nPick topic number (Enter = 1): ").strip()
        if raw == "":
            return candidates[0]
        if raw.isdigit() and 1 <= int(raw) <= len(candidates):
            return candidates[int(raw) - 1]
        print(f"Enter a number between 1 and {len(candidates)}.")


def _meta(path: Path) -> dict[str, str]:
    head = "\n".join(path.read_text(encoding="utf-8").splitlines()[:20])
    return dict(_META.findall(head))


def _entry(week: str, path: Path, fallback_topic: str, status: str) -> CalendarEntry:
    meta = _meta(path)
    is_video = "video" in path.stem
    slot = meta.get("slot") if not is_video else None
    return CalendarEntry(
        week=week,
        type="video" if is_video else "reel",
        topic=meta.get("topic") or meta.get("idea") or fallback_topic,
        file=rel(path),
        status=status,
        slot=slot if slot in _SLOTS else None,
    )


async def factcheck(week: str, draft: Path, sem: asyncio.Semaphore) -> FactCheckVerdict:
    async with sem:
        await run_query(checker_task(week, rel(draft)), checker_options(week), f"check:{draft.stem}")
    report = report_path_for(draft)
    if not report.exists():
        return FactCheckVerdict(draft=rel(draft), verdict="FAIL", report_path=rel(report),
                                required_fixes=["fact-check report was not written — re-run the checker"])
    text = report.read_text(encoding="utf-8")
    verdict = store.parse_verdict(text) or "FAIL"
    fixes = store.parse_required_fixes(text) if verdict == "FAIL" else []
    if verdict == "FAIL" and not fixes:
        fixes = [f"report {rel(report)} says FAIL but lists no fixes — read it"]
    return FactCheckVerdict(draft=rel(draft), verdict=verdict, report_path=rel(report), required_fixes=fixes)


async def factcheck_loop(week: str, editor: ClaudeSDKClient, drafts: list[Path]) -> tuple[list[Path], list[Path]]:
    sem = asyncio.Semaphore(CHECKER_CONCURRENCY)
    pending = list(drafts)
    passed: list[Path] = []
    failed: dict[Path, FactCheckVerdict] = {}
    for attempt in range(FACTCHECK_MAX_RETRIES + 1):
        verdicts = await asyncio.gather(*(factcheck(week, d, sem) for d in pending))
        failed = {d: v for d, v in zip(pending, verdicts) if v.verdict != "PASS"}
        passed.extend(d for d in pending if d not in failed)
        for d, v in failed.items():
            _log("check", f"FAIL {rel(d)}: " + " | ".join(v.required_fixes[:3]))
        if not failed:
            break
        if attempt == FACTCHECK_MAX_RETRIES:
            _log("check", f"{len(failed)} draft(s) still failing after {attempt} revision round(s) — fix by hand, then re-run finalize")
            break
        _log("editor", f"revising {len(failed)} draft(s)")
        await editor.query(revise_task(week, {rel(d): v.required_fixes for d, v in failed.items()}))
        await drain(editor, "editor")
        pending = list(failed)
    return passed, list(failed)


async def run_weekly(week: str, auto: bool, topic_index: int | None, stop_at: str,
                     x_enabled: bool | None = None) -> None:
    x_on = X_TRENDS_ENABLED if x_enabled is None else x_enabled
    beats = scout_beats(x_on)
    x_note = f", X trends ON (max {X_MAX_API_CALLS} billable calls)" if x_on else ", X trends off"
    _log("weekly", f"week {week} — stage A: trend scouts ({len(beats)} in parallel{x_note})")
    statuses = await run_scouts(week, beats)
    if all(s.startswith(("crashed", "missing")) for s in statuses.values()):
        raise SystemExit("all scouts failed — check ANTHROPIC_API_KEY and network, then re-run")

    _log("weekly", "stage B: editor session")
    async with ClaudeSDKClient(options=editor_options(week, x_on)) as editor:
        await editor.query(rank_task(week, beats))
        await drain(editor, "editor")
        candidates_path = research_dir(week) / "candidates.md"
        if not candidates_path.exists():
            raise SystemExit("editor did not write candidates.md")
        candidates = parse_candidates(candidates_path.read_text(encoding="utf-8"))
        if not candidates:
            raise SystemExit(f"could not parse any candidates from {rel(candidates_path)}")
        chosen = choose(candidates, auto, topic_index)
        _log("weekly", f"topic: {chosen.title}")

        await editor.query(write_task(week, chosen.title))
        await drain(editor, "editor")
        drafts = sorted(content_dir(week).glob("DRAFT-*.md"))
        if not drafts:
            raise SystemExit("editor wrote no DRAFT-*.md files")
        for d in drafts:
            store.append_calendar(_entry(week, d, chosen.title, "draft"))
        _log("weekly", f"{len(drafts)} drafts in {rel(content_dir(week))}/")
        if stop_at == "draft":
            if x_on:
                _log("weekly", f"X usage: {x_tools.ledger_summary()}")
            _log("weekly", "stopped at draft stage — review the drafts, then `python run.py check` to fact-check them")
            return

        _log("weekly", "stage C: independent fact-check")
        passed, failed = await factcheck_loop(week, editor, drafts)

    _log("weekly", f"fact-check: {len(passed)} PASS, {len(failed)} FAIL")
    if x_on:
        _log("weekly", f"X usage: {x_tools.ledger_summary()}")
    _log("weekly", f"next: read {rel(content_dir(week))}/DRAFT-*.md, fill the [YUGANSH: ...] fields, then `python run.py finalize`")


async def run_check(week: str) -> None:
    drafts = sorted(content_dir(week).glob("DRAFT-*.md"))
    if not drafts:
        raise SystemExit(f"no drafts in {rel(content_dir(week))} — run `python run.py weekly` first")
    _log("check", f"fact-checking {len(drafts)} draft(s) for {week}")
    async with ClaudeSDKClient(options=editor_options(week, X_TRENDS_ENABLED)) as editor:
        passed, failed = await factcheck_loop(week, editor, drafts)
    _log("check", f"{len(passed)} PASS, {len(failed)} FAIL")
    if not failed:
        _log("check", "all drafts passed — fill the [YUGANSH: ...] fields, then `python run.py finalize`")


def finalize(week: str) -> None:
    cd = content_dir(week)
    state = store.calendar_state()
    done = blocked = 0
    for draft in sorted(cd.glob("DRAFT-*.md")):
        final = cd / draft.name.replace("DRAFT-", "FINAL-", 1)
        ok, reason = gate_check(final)
        if not ok:
            blocked += 1
            _log("finalize", f"BLOCKED {final.name}: {reason}")
            continue
        shutil.copyfile(draft, final)
        prior = state.get(rel(draft))
        store.append_calendar(_entry(week, final, prior.topic if prior else draft.stem, "final"))
        done += 1
        _log("finalize", f"OK {rel(final)}")
    _log("finalize", f"{done} finalized, {blocked} blocked")


async def run_daily(week: str, fresh: bool) -> None:
    cd = content_dir(week)
    state = store.calendar_state()

    def unused(pattern: str, status: str) -> list[Path]:
        return [p for p in sorted(cd.glob(pattern)) if (e := state.get(rel(p))) and e.status == status]

    pool = unused("FINAL-reel-*.md", "final")
    if not pool:
        pool = unused("DRAFT-reel-*.md", "draft")
        if pool:
            _log("daily", "no fact-checked FINAL reels yet — picking from DRAFTs; finalize before recording")
    if not pool:
        raise SystemExit(f"no unused reels for {week} — run `python run.py weekly` first")

    result = await run_query(daily_pick_task(week, [rel(p) for p in pool], fresh), daily_options(week, fresh), "daily")
    match = re.search(r"PICK:\s*(\S+)", result.text)
    pick = (PROJECT_ROOT / match.group(1)).resolve() if match else pool[0]
    if pick not in [p.resolve() for p in pool]:
        pick = pool[0]
    prior = state.get(rel(pick))
    store.append_calendar(_entry(week, pick, prior.topic if prior else pick.stem, "used"))
    _log("daily", f"today's reel: {rel(pick)}")
    print("\n" + pick.read_text(encoding="utf-8"))


async def doctor() -> None:
    auth = await run_query(
        "Reply with exactly the word OK and nothing else.",
        ClaudeAgentOptions(system_prompt="You are a connectivity probe.", allowed_tools=[], permission_mode="dontAsk",
                           cwd=PROJECT_ROOT, model=SCOUT_MODEL, max_turns=1, max_budget_usd=0.2),
        "doctor:auth",
    )
    auth_ok = not auth.is_error and "OK" in auth.text
    print(f"auth      : {'ok' if auth_ok else 'FAILED — ' + auth.text[:300]}")
    if not auth_ok:
        return
    search = await run_query(
        "Use the WebSearch tool exactly once to search for: Berlin Marathon 2026 results. "
        "Then reply with only the title of the first result.",
        ClaudeAgentOptions(system_prompt="You are a connectivity probe.", allowed_tools=["WebSearch"],
                           permission_mode="dontAsk", cwd=PROJECT_ROOT, model=SCOUT_MODEL, max_turns=3, max_budget_usd=0.5),
        "doctor:websearch",
    )
    search_ok = "WebSearch" in search.tool_calls and not search.is_error and bool(search.text.strip())
    print(f"websearch : {'ok — ' + search.text.strip()[:120] if search_ok else 'FAILED — ' + (search.text[:300] or 'no WebSearch call happened')}")

    if X_TRENDS_ENABLED:
        x_ok, x_msg = await x_tools.probe()
        print(f"x-trends  : {'ok — ' + x_msg if x_ok else 'FAILED — ' + x_msg}")
        print(f"x usage   : {x_tools.ledger_summary()}")
    else:
        print("x-trends  : disabled (X_TRENDS_ENABLED=false) — pipeline runs on the 4 web-based beats")

    print(f"claude cost: ${auth.cost_usd + search.cost_usd:.3f}")
