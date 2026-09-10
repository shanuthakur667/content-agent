from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from pydantic import ValidationError

from .config import CALENDAR_FILE, EVENT_CALENDAR_FILE, EVERGREEN_FILE
from .schemas import CalendarEntry, ScoutFinding

_FINDING_HEADING = re.compile(r"^###\s+F(\d+)\s*$", re.MULTILINE)
_KV_LINE = re.compile(r"^([a-z_]+):\s*(.*)$")
_STATUS_LINE = re.compile(r"^status:\s*(ok|no-news|search-failed)\s*$", re.MULTILINE)
_VERDICT_LINE = re.compile(r"^VERDICT:\s*(PASS|FAIL)\s*$", re.MULTILINE)
_PLACEHOLDER = re.compile(r"\{[^}]*\}")
_TABLE_ROW = re.compile(r"^\|(.+)\|\s*$")


@dataclass
class ScoutReport:
    status: str | None
    findings: list[ScoutFinding]
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def _strip_html_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)


def parse_scout_report(text: str) -> ScoutReport:
    text = _strip_html_comments(text)
    problems: list[str] = []

    m = _STATUS_LINE.search(text)
    status = m.group(1) if m else None
    if status is None:
        problems.append("missing or invalid `status:` line (must be ok | no-news | search-failed)")

    findings: list[ScoutFinding] = []
    parts = _FINDING_HEADING.split(text)
    for i in range(1, len(parts), 2):
        fid = f"F{parts[i]}"
        body = parts[i + 1].split("\n## ", 1)[0]
        fields: dict[str, str] = {}
        for line in body.splitlines():
            kv = _KV_LINE.match(line.strip())
            if kv:
                fields[kv.group(1)] = kv.group(2).strip().strip('"')
        if not fields:
            continue
        unfilled = [k for k, v in fields.items() if _PLACEHOLDER.search(v) or not v]
        if unfilled:
            problems.append(f"{fid}: unfilled fields {unfilled}")
            continue
        try:
            findings.append(ScoutFinding(**fields))
        except ValidationError as e:
            for err in e.errors():
                problems.append(f"{fid}: {'.'.join(str(x) for x in err['loc'])} — {err['msg']}")
        except TypeError as e:
            problems.append(f"{fid}: {e}")

    if status == "ok" and not findings and not problems:
        problems.append("status is `ok` but no findings were parsed")
    if status == "no-news" and findings:
        problems.append("status is `no-news` but findings are present — use `ok`")
    return ScoutReport(status=status, findings=findings, problems=problems)


def parse_verdict(text: str) -> str | None:
    m = _VERDICT_LINE.search(text)
    return m.group(1) if m else None


def parse_required_fixes(text: str) -> list[str]:
    section = text.split("## Required fixes", 1)
    if len(section) < 2:
        return []
    fixes = []
    for line in section[1].splitlines():
        s = line.strip()
        if re.match(r"^\d+\.\s+\S", s):
            fixes.append(re.sub(r"^\d+\.\s+", "", s))
    return fixes


def _table_rows(text: str) -> list[list[str]]:
    rows = []
    for line in text.splitlines():
        m = _TABLE_ROW.match(line.strip())
        if not m:
            continue
        cells = [c.strip() for c in m.group(1).split("|")]
        if all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
            continue
        rows.append(cells)
    return rows


def _parse_date(s: str) -> date | None:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def load_events(path: Path = EVENT_CALENDAR_FILE) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    verified_part, _, unverified_part = text.partition("## Unverified")
    events = []
    for verified, chunk in ((True, verified_part), (False, unverified_part)):
        for cells in _table_rows(chunk):
            if cells[0].lower() == "date":
                continue
            start = _parse_date(cells[0]) if cells else None
            events.append({
                "date": start.isoformat() if start else None,
                "end_date": cells[1] or None if len(cells) > 1 else None,
                "event": cells[2] if len(cells) > 2 else "",
                "type": cells[3] if len(cells) > 3 else "",
                "why_it_matters": cells[4] if len(cells) > 4 else "",
                "verified": verified,
            })
    return events


def load_evergreen(path: Path = EVERGREEN_FILE) -> list[dict]:
    items = []
    for cells in _table_rows(path.read_text(encoding="utf-8")):
        if cells[0] in ("#", "") or not cells[0].isdigit():
            continue
        items.append({
            "id": int(cells[0]),
            "topic": cells[1],
            "angle": cells[2],
            "format": cells[3],
            "status": cells[4] or "open",
            "last_used": cells[5] or None,
        })
    return items


def read_calendar(path: Path = CALENDAR_FILE) -> list[CalendarEntry]:
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(CalendarEntry.model_validate_json(line))
        except ValidationError:
            continue
    return entries


def append_calendar(entry: CalendarEntry, path: Path = CALENDAR_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(entry.model_dump_json() + "\n")


def calendar_state(path: Path = CALENDAR_FILE) -> dict[str, CalendarEntry]:
    """Latest entry per file wins; the log itself stays append-only."""
    state: dict[str, CalendarEntry] = {}
    for e in read_calendar(path):
        state[e.file] = e
    return state


def dump_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)
