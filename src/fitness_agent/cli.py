from __future__ import annotations

import argparse
import asyncio
import os
import sys

from . import pipeline
from .config import week_id


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(prog="run.py", description="Research + script pipeline for the channel")
    sub = parser.add_subparsers(dest="cmd", required=True)

    weekly = sub.add_parser("weekly", help="scouts -> rank -> research -> drafts -> fact-check")
    weekly.add_argument("--week", help="ISO week id like 2026-W37 (default: current week)")
    weekly.add_argument("--auto", action="store_true", help="take the top-ranked topic without asking")
    weekly.add_argument("--topic", type=int, help="pick candidate N without asking")
    weekly.add_argument("--stop-at", choices=["draft", "check"], default="check",
                        help="stop after drafts are written, or after fact-check (default)")

    daily = sub.add_parser("daily", help="pick today's reel from this week's bank")
    daily.add_argument("--week")
    daily.add_argument("--no-fresh", action="store_true", help="skip the quick breaking-news check")

    check = sub.add_parser("check", help="fact-check existing DRAFT-*.md (also after hand edits), revising on FAIL")
    check.add_argument("--week")

    fin = sub.add_parser("finalize", help="DRAFT-* -> FINAL-* for every draft with a fresh PASS report")
    fin.add_argument("--week")

    sub.add_parser("doctor", help="two cheap calls: is auth working, does WebSearch work")

    args = parser.parse_args(argv)
    week = getattr(args, "week", None) or week_id()

    if not os.getenv("ANTHROPIC_API_KEY") and not os.getenv("ANTHROPIC_AUTH_TOKEN"):
        print("warning: no ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN set (see .env.example)", file=sys.stderr)

    if args.cmd == "doctor":
        asyncio.run(pipeline.doctor())
    elif args.cmd == "weekly":
        asyncio.run(pipeline.run_weekly(week, args.auto, args.topic, args.stop_at))
    elif args.cmd == "daily":
        asyncio.run(pipeline.run_daily(week, not args.no_fresh))
    elif args.cmd == "check":
        asyncio.run(pipeline.run_check(week))
    elif args.cmd == "finalize":
        pipeline.finalize(week)
