from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

CHANNEL_DIR = PROJECT_ROOT / "channel"
TEMPLATES_DIR = PROJECT_ROOT / "templates"
RESEARCH_DIR = PROJECT_ROOT / "research"
CONTENT_DIR = PROJECT_ROOT / "content"
CALENDAR_FILE = CONTENT_DIR / "calendar.jsonl"
EVENT_CALENDAR_FILE = CHANNEL_DIR / "event-calendar.md"
EVERGREEN_FILE = CHANNEL_DIR / "evergreen-backlog.md"

SCOUT_BEATS = ("events-news", "discourse", "science-gear", "competitor")

SCOUT_MODEL = os.getenv("SCOUT_MODEL", "claude-sonnet-5")
RESEARCHER_MODEL = os.getenv("RESEARCHER_MODEL", "claude-sonnet-5")
EDITOR_MODEL = os.getenv("EDITOR_MODEL", "claude-opus-5")
CHECKER_MODEL = os.getenv("CHECKER_MODEL", "claude-opus-5")

SCOUT_MAX_TURNS = int(os.getenv("SCOUT_MAX_TURNS", "40"))
EDITOR_MAX_TURNS = int(os.getenv("EDITOR_MAX_TURNS", "80"))
CHECKER_MAX_TURNS = int(os.getenv("CHECKER_MAX_TURNS", "40"))
FACTCHECK_MAX_RETRIES = int(os.getenv("FACTCHECK_MAX_RETRIES", "2"))
CHECKER_CONCURRENCY = int(os.getenv("CHECKER_CONCURRENCY", "4"))

SCOUT_MAX_BUDGET_USD = float(os.getenv("SCOUT_MAX_BUDGET_USD", "3"))
EDITOR_MAX_BUDGET_USD = float(os.getenv("EDITOR_MAX_BUDGET_USD", "15"))
CHECKER_MAX_BUDGET_USD = float(os.getenv("CHECKER_MAX_BUDGET_USD", "3"))


def week_id(d: date | None = None) -> str:
    d = d or date.today()
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def research_dir(week: str) -> Path:
    p = RESEARCH_DIR / week
    p.mkdir(parents=True, exist_ok=True)
    return p


def content_dir(week: str) -> Path:
    p = CONTENT_DIR / week
    p.mkdir(parents=True, exist_ok=True)
    return p


def now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def rel(path: Path) -> str:
    """Project-relative POSIX path, the form used in prompts and reports."""
    return path.resolve().relative_to(PROJECT_ROOT).as_posix()
