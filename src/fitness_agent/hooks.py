from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

from . import store
from .config import PROJECT_ROOT, RESEARCH_DIR, rel

WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")


def _deny(reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _feedback(reason: str) -> dict:
    return {"decision": "block", "reason": reason}


def _target(tool_input: dict) -> Path | None:
    raw = tool_input.get("file_path") or tool_input.get("path") or tool_input.get("notebook_path")
    if not raw:
        return None
    p = Path(raw)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p.resolve()


def _safe_rel(p: Path) -> str:
    try:
        return rel(p)
    except ValueError:
        return str(p)


def _core(name: str) -> str:
    """'DRAFT-reel-01.md' -> 'reel-01'; 'FINAL-video.md' -> 'video'."""
    stem = Path(name).stem
    return stem.split("-", 1)[1] if "-" in stem else stem


def report_path_for(script: Path) -> Path:
    return RESEARCH_DIR / script.parent.name / f"fact-check-{_core(script.name)}.md"


def gate_check(final_path: Path) -> tuple[bool, str]:
    draft = final_path.parent / f"DRAFT-{_core(final_path.name)}.md"
    report = report_path_for(final_path)
    if not draft.exists():
        return False, f"no draft {_safe_rel(draft)} to finalize"
    if not report.exists():
        return False, f"no fact-check report at {_safe_rel(report)} — run the fact-checker first"
    verdict = store.parse_verdict(report.read_text(encoding="utf-8"))
    if verdict != "PASS":
        return False, f"{_safe_rel(report)} says VERDICT: {verdict or 'missing'} — fix the draft and re-check"
    if report.stat().st_mtime < draft.stat().st_mtime:
        return False, f"{_safe_rel(draft)} was modified after its PASS report — re-run the fact-checker"
    return True, "ok"


def make_scope_guard(allow: list[tuple[Path, str]]):
    resolved = [(d.resolve(), pattern) for d, pattern in allow]

    async def scope_guard(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
        if input_data.get("tool_name") not in WRITE_TOOLS:
            return {}
        target = _target(input_data.get("tool_input") or {})
        if target is None:
            return {}
        for directory, pattern in resolved:
            if target.parent == directory and fnmatch.fnmatch(target.name, pattern):
                return {}
        allowed = ", ".join(f"{_safe_rel(d)}/{p}" for d, p in resolved)
        return _deny(f"scope_guard: this agent may only write {allowed}. Attempted: {_safe_rel(target)}")

    return scope_guard


async def final_gate(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
    tool_name = input_data.get("tool_name")
    tool_input = input_data.get("tool_input") or {}
    if tool_name in ("Bash", "PowerShell"):
        if "FINAL-" in (tool_input.get("command") or ""):
            return _deny("final_gate: FINAL-* files are produced only by `python run.py finalize` after a PASS fact-check.")
        return {}
    if tool_name not in WRITE_TOOLS:
        return {}
    target = _target(tool_input)
    if target is None or not target.name.startswith("FINAL-"):
        return {}
    ok, reason = gate_check(target)
    return {} if ok else _deny(f"final_gate: {reason}")


async def scout_validate(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
    if input_data.get("tool_name") not in WRITE_TOOLS:
        return {}
    target = _target(input_data.get("tool_input") or {})
    if target is None or not target.exists() or not fnmatch.fnmatch(target.name, "scout-*.md"):
        return {}
    if RESEARCH_DIR.resolve() not in target.parents:
        return {}
    report = store.parse_scout_report(target.read_text(encoding="utf-8"))
    if report.ok:
        return {}
    problems = "\n".join(f"- {p}" for p in report.problems[:12])
    return _feedback(
        f"scout-validate rejected {_safe_rel(target)}. Fix these and write the WHOLE file again:\n{problems}"
    )


async def report_validate(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
    if input_data.get("tool_name") not in WRITE_TOOLS:
        return {}
    target = _target(input_data.get("tool_input") or {})
    if target is None or not target.exists() or not fnmatch.fnmatch(target.name, "fact-check-*.md"):
        return {}
    text = target.read_text(encoding="utf-8")
    verdict = store.parse_verdict(text)
    if verdict is None:
        return _feedback("report-validate: the report must contain a line that is exactly `VERDICT: PASS` or `VERDICT: FAIL`.")
    if verdict == "FAIL" and not store.parse_required_fixes(text):
        return _feedback("report-validate: VERDICT is FAIL but `## Required fixes` has no numbered items. List the specific fixes.")
    return {}
