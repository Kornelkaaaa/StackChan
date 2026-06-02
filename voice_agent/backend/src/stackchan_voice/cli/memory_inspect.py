"""Read-only `memory.db` inspector.

Installed as the console script `stackchan-memory` (see pyproject.toml).
Uses stdlib `sqlite3` rather than `aiosqlite` because this is a one-shot
synchronous CLI — no event loop in sight.

Subcommands:
    stackchan-memory list                List all sessions (newest first)
    stackchan-memory show <session_id>   Full detail of one session
    stackchan-memory stats               High-level counts

The DB path defaults to $DB_PATH if set, then `./memory.db`. Override with --db.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import textwrap
from pathlib import Path
from typing import Sequence


def _resolve_db_path(arg: str | None) -> Path:
    if arg:
        return Path(arg)
    env = os.environ.get("DB_PATH")
    if env:
        return Path(env)
    return Path("memory.db")


def _connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise SystemExit(f"db not found: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _row(values: Sequence[str], widths: Sequence[int]) -> str:
    return "  ".join(v.ljust(w) for v, w in zip(values, widths))


def cmd_list(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT id, started_at, ended_at, end_reason, turn_count, "
        "       CASE WHEN summary IS NULL THEN '-' ELSE 'y' END AS has_summary "
        "FROM sessions ORDER BY id DESC"
    ).fetchall()
    if not rows:
        print("(no sessions)")
        return

    headers = ("id", "started", "ended", "reason", "turns", "sum")
    widths = (4, 27, 27, 18, 5, 3)
    print(_row(headers, widths))
    print(_row(tuple("-" * w for w in widths), widths))
    for r in rows:
        print(_row(
            (
                str(r["id"]),
                r["started_at"] or "-",
                r["ended_at"] or "-",
                r["end_reason"] or "-",
                str(r["turn_count"]),
                r["has_summary"],
            ),
            widths,
        ))


def cmd_show(conn: sqlite3.Connection, session_id: int) -> None:
    s = conn.execute(
        "SELECT * FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if s is None:
        raise SystemExit(f"session {session_id} not found")

    print(f"Session #{s['id']}")
    print(f"  started:  {s['started_at']}")
    print(f"  ended:    {s['ended_at'] or '-'}")
    print(f"  reason:   {s['end_reason'] or '-'}")
    print(f"  turns:    {s['turn_count']}")
    print()

    turns = conn.execute(
        "SELECT * FROM turns WHERE session_id = ? ORDER BY turn_index",
        (session_id,),
    ).fetchall()
    if not turns:
        print("  (no turns)")
    else:
        for t in turns:
            print(f"  Turn {t['turn_index']}  ({t['started_at']} → {t['completed_at']})")
            if t["user_transcript"]:
                for line in textwrap.wrap(
                    f"Kornelia: {t['user_transcript']}", width=78,
                    subsequent_indent="              ",
                ):
                    print(f"    {line}")
            if t["model_transcript"]:
                for line in textwrap.wrap(
                    f"Stack-chan: {t['model_transcript']}", width=78,
                    subsequent_indent="                ",
                ):
                    print(f"    {line}")
            print()

    if s["summary"]:
        print(f"Summary  ({s['summary_model']}):")
        for line in textwrap.wrap(s["summary"], width=78):
            print(f"  {line}")


def cmd_stats(conn: sqlite3.Connection) -> None:
    n_sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    n_turns = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
    n_summarized = conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE summary IS NOT NULL"
    ).fetchone()[0]
    by_reason = conn.execute(
        "SELECT end_reason, COUNT(*) AS n FROM sessions "
        "WHERE end_reason IS NOT NULL GROUP BY end_reason ORDER BY n DESC"
    ).fetchall()

    print(f"Sessions:    {n_sessions}")
    print(f"Turns:       {n_turns}")
    print(f"Summarized:  {n_summarized}")
    if by_reason:
        print("End reasons:")
        for r in by_reason:
            print(f"  {r['end_reason']:<20} {r['n']}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="stackchan-memory",
        description="Read-only inspector for the Stack-chan memory.db",
    )
    parser.add_argument(
        "--db",
        help="Path to memory.db (default: $DB_PATH or ./memory.db)",
    )

    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="List all sessions (newest first)")
    show = sub.add_parser("show", help="Show one session in detail")
    show.add_argument("session_id", type=int)
    sub.add_parser("stats", help="High-level counts")

    args = parser.parse_args(argv)

    # Windows consoles often default to a legacy code page (e.g. cp1250) that
    # can't encode the glyphs we print (→) or non-ASCII transcript text, which
    # would crash with UnicodeEncodeError. Force UTF-8 with a safe fallback.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    db_path = _resolve_db_path(args.db)
    conn = _connect(db_path)
    try:
        if args.cmd == "list":
            cmd_list(conn)
        elif args.cmd == "show":
            cmd_show(conn, args.session_id)
        elif args.cmd == "stats":
            cmd_stats(conn)
        else:  # argparse guarantees one of the above, but be explicit
            sys.exit(f"unknown command: {args.cmd}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
