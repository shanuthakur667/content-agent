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

BASE_SCOUT_BEATS = ("events-news", "discourse", "science-gear", "competitor")
X_BEAT = "x-trends"


def _flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# Feature gate: when off, the pipeline behaves exactly as it did before X existed.
X_TRENDS_ENABLED = _flag("X_TRENDS_ENABLED", False)
X_BEARER_TOKEN = os.getenv("X_BEARER_TOKEN", "").strip()
X_API_BASE = os.getenv("X_API_BASE", "https://api.x.com/2").rstrip("/")


def _woeids() -> tuple[int, ...]:
    out = []
    for part in os.getenv("X_TRENDS_WOEIDS", "1,23424848").split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return tuple(out) or (1,)


# 1 = Worldwide (documented). 23424848 = India (legacy Yahoo WOEID, NOT in X's published table —
# the trends tool surfaces a clear validation error if X rejects it).
X_TRENDS_WOEIDS = _woeids()

# X bills per request for trends and PER POST RETURNED for search, so both a call count and a
# dollar ceiling are enforced in code — the model cannot spend past these by choosing to.
X_MAX_API_CALLS = int(os.getenv("X_MAX_API_CALLS", "12"))
X_MAX_SPEND_USD = float(os.getenv("X_MAX_SPEND_USD", "0.50"))
X_SEARCH_MAX_RESULTS = int(os.getenv("X_SEARCH_MAX_RESULTS", "25"))


def scout_beats(x_enabled: bool | None = None) -> tuple[str, ...]:
    enabled = X_TRENDS_ENABLED if x_enabled is None else x_enabled
    return (*BASE_SCOUT_BEATS, X_BEAT) if enabled else BASE_SCOUT_BEATS


def x_config_error(x_enabled: bool | None = None) -> str | None:
    """A reason string when X is switched on but unusable, else None.

    'Disabled' is a valid state and never an error; 'enabled but no credential' is a
    misconfiguration we fail fast on, rather than silently running without the signal.
    """
    enabled = X_TRENDS_ENABLED if x_enabled is None else x_enabled
    if not enabled:
        return None
    if not X_BEARER_TOKEN:
        return (
            "X_TRENDS_ENABLED is on but X_BEARER_TOKEN is empty. Add the token to .env, "
            "or set X_TRENDS_ENABLED=false to run without the X signal."
        )
    return None


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
