"""
Study Pilot - a self-hosted MCP server for Alexa+ that remembers your exams
and study sessions across conversations, and tells you what to study next.

Transport : Streamable HTTP  (endpoint: http://HOST:PORT/mcp)
MCP spec  : 2025-11-25       (mcp Python SDK 1.30.0)

Run:  python server.py            -> starts the server
      python server.py --reset    -> wipes saved data first (clean demo)
"""

from __future__ import annotations

import argparse
import json
import os
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse

# ---------------------------------------------------------------------------
# Storage: one small JSON file, so memory survives server restarts and new
# conversations. A lock stops two requests from writing at the same time.
# ---------------------------------------------------------------------------
DATA_FILE = Path(
    os.environ.get("STUDY_PILOT_DATA", Path(__file__).parent / "data" / "study_data.json")
)
_lock = threading.Lock()


def _empty() -> dict[str, Any]:
    return {"exams": {}, "sessions": []}


def load() -> dict[str, Any]:
    if not DATA_FILE.exists():
        return _empty()
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return _empty()


def save(data: dict[str, Any]) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(DATA_FILE)  # atomic swap, so a crash never leaves half a file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_DATE_FORMATS_WITH_YEAR = ["%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y", "%d/%m/%Y"]
_DATE_FORMATS_NO_YEAR = ["%d %b", "%d %B", "%b %d", "%B %d"]


def parse_date(text: str) -> date:
    """Accepts '2026-11-16', '16 Nov', 'Nov 16', 'today', 'tomorrow', ..."""
    raw = text.strip().lower().replace(",", "")
    today = date.today()
    if raw == "today":
        return today
    if raw == "tomorrow":
        return today + timedelta(days=1)
    cleaned = text.strip().replace(",", "")
    for fmt in _DATE_FORMATS_WITH_YEAR:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            pass
    for fmt in _DATE_FORMATS_NO_YEAR:
        try:
            d = datetime.strptime(f"{cleaned} {today.year}", f"{fmt} %Y").date()
            return d if d >= today else d.replace(year=today.year + 1)
        except ValueError:
            pass
    raise ValueError(f"I couldn't understand the date '{text}'. Try something like '16 Nov' or '2026-11-16'.")


def find_exam(data: dict[str, Any], subject: str) -> str:
    """Return the stored key for a subject. Allows partial names ('maths' -> 'engineering maths')."""
    key = subject.strip().lower()
    exams = data["exams"]
    if key in exams:
        return key
    matches = [k for k in exams if key in k or k in key]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(f"You don't have an exam called '{subject}' yet. Add it first.")
    raise ValueError(f"'{subject}' matches more than one exam: {', '.join(matches)}. Which one?")


def days_left(exam: dict[str, Any], on: date | None = None) -> int:
    return (date.fromisoformat(exam["date"]) - (on or date.today())).days


def topics_left(exam: dict[str, Any]) -> list[str]:
    return [t["name"] for t in exam["topics"] if not t["done"]]


def recent_minutes(data: dict[str, Any], key: str, days: int = 3) -> int:
    since = date.today() - timedelta(days=days)
    return sum(
        s["minutes"]
        for s in data["sessions"]
        if s["subject_key"] == key and date.fromisoformat(s["date"]) > since
    )


def priority(data: dict[str, Any], key: str, on: date | None = None, use_recent: bool = True) -> float:
    """
    Urgency score. Higher = study this first.
      more topics left    -> higher
      fewer days left     -> higher
      studied it recently -> lower (so subjects get rotated)
    """
    exam = data["exams"][key]
    d = days_left(exam, on)
    if d < 0:
        return 0.0
    score = (len(topics_left(exam)) + 1) / (d + 1)
    if use_recent:
        score /= 1 + recent_minutes(data, key) / 60
    return score


def upcoming(data: dict[str, Any], on: date | None = None) -> list[str]:
    keys = [k for k, e in data["exams"].items() if days_left(e, on) >= 0]
    return sorted(keys, key=lambda k: data["exams"][k]["date"])


def when(d: int) -> str:
    return "today" if d == 0 else "tomorrow" if d == 1 else f"in {d} days"


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------
mcp = FastMCP(
    name="Study Pilot",
    instructions=(
        "Study Pilot is a voice-first exam and revision planner for students. "
        "It remembers exams, syllabus topics and study sessions across conversations. "
        "Every tool returns a short 'speech' field written to be read aloud by Alexa. "
        "Convert spoken dates to a form like '16 Nov' before calling tools."
    ),
    host=os.environ.get("HOST", "127.0.0.1"),
    port=int(os.environ.get("PORT", "8000")),
)


@mcp.tool(
    title="Add or update an exam",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True),
)
def add_exam(subject: str, exam_date: str, topics: list[str] | None = None) -> dict[str, Any]:
    """Save an exam (subject + date) and optionally its syllabus topics. Calling it again updates the date and adds new topics."""
    when_date = parse_date(exam_date)
    with _lock:
        data = load()
        key = subject.strip().lower()
        exam = data["exams"].setdefault(key, {"subject": subject.strip(), "date": "", "topics": []})
        exam["date"] = when_date.isoformat()
        known = {t["name"].lower() for t in exam["topics"]}
        for t in topics or []:
            if t.strip() and t.strip().lower() not in known:
                exam["topics"].append({"name": t.strip(), "done": False})
                known.add(t.strip().lower())
        save(data)
    d = days_left(exam)
    n = len(exam["topics"])
    speech = f"Got it. {exam['subject']} is on {when_date.strftime('%A, %d %B')}, {when(d)}."
    if n:
        speech += f" I'm tracking {n} topic{'s' if n != 1 else ''} for it."
    return {"speech": speech, "exam": exam, "days_left": d}


@mcp.tool(
    title="Log a study session",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False),
)
def log_study_session(
    subject: str, minutes: int, topic: str | None = None, topic_finished: bool = False
) -> dict[str, Any]:
    """Record that the student studied a subject for some minutes. If topic_finished is true, that topic is marked done."""
    if minutes <= 0 or minutes > 24 * 60:
        raise ValueError("Minutes should be between 1 and 1440.")
    with _lock:
        data = load()
        key = find_exam(data, subject)
        exam = data["exams"][key]
        data["sessions"].append(
            {
                "subject_key": key,
                "minutes": minutes,
                "topic": topic,
                "date": date.today().isoformat(),
                "logged_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        marked = None
        if topic and topic_finished:
            for t in exam["topics"]:
                if t["name"].lower() == topic.strip().lower() or topic.strip().lower() in t["name"].lower():
                    t["done"] = True
                    marked = t["name"]
                    break
            else:
                exam["topics"].append({"name": topic.strip(), "done": True})
                marked = topic.strip()
        save(data)
    total = sum(s["minutes"] for s in data["sessions"] if s["subject_key"] == key)
    left = topics_left(exam)
    speech = f"Nice work. Logged {minutes} minutes of {exam['subject']}."
    if marked:
        speech += f" {marked} is done."
    speech += f" That's {total} minutes total"
    speech += f", with {len(left)} topic{'s' if len(left) != 1 else ''} to go." if exam["topics"] else "."
    return {"speech": speech, "total_minutes": total, "topics_left": left}


@mcp.tool(
    title="What should I study now?",
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
)
def what_should_i_study(available_minutes: int = 60) -> dict[str, Any]:
    """Pick the single most urgent subject and topic for the time the student has right now."""
    data = load()
    keys = upcoming(data)
    if not keys:
        return {"speech": "You have no upcoming exams saved. Tell me your next exam and its date.", "pick": None}
    ranked = sorted(keys, key=lambda k: priority(data, k), reverse=True)
    best = data["exams"][ranked[0]]
    left = topics_left(best)
    d = days_left(best)
    topic_part = f", starting with {left[0]}" if left else ""
    speech = (
        f"Spend your {available_minutes} minutes on {best['subject']}{topic_part}. "
        f"It's {when(d)}"
        + (f" and you still have {len(left)} topic{'s' if len(left) != 1 else ''} left." if left else ".")
    )
    if len(ranked) > 1:
        speech += f" After that, {data['exams'][ranked[1]]['subject']} is next."
    return {
        "speech": speech,
        "pick": {"subject": best["subject"], "topic": left[0] if left else None, "days_left": d},
        "ranking": [
            {"subject": data["exams"][k]["subject"], "score": round(priority(data, k), 3)} for k in ranked
        ],
    }


@mcp.tool(
    title="Build a revision plan",
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
)
def plan_revision(days: int = 7, minutes_per_day: int = 120) -> dict[str, Any]:
    """Create a day-by-day revision timetable across all upcoming exams, in 15-minute blocks. The day before an exam is reserved for that subject."""
    days = max(1, min(days, 30))
    minutes_per_day = max(15, min(minutes_per_day, 12 * 60))
    data = load()
    if not upcoming(data):
        return {"speech": "You have no upcoming exams saved yet.", "plan": []}

    plan = []
    cursor = {k: 0 for k in data["exams"]}  # which topic each subject is up to
    for i in range(days):
        day = date.today() + timedelta(days=i)
        live = [k for k in upcoming(data, day) if days_left(data["exams"][k], day) > 0]
        if not live:
            break
        eve = [k for k in live if days_left(data["exams"][k], day) == 1]
        if eve:  # day before an exam: all-in on that subject
            shares = {eve[0]: minutes_per_day}
        else:
            scores = {k: priority(data, k, on=day, use_recent=False) for k in live}
            total = sum(scores.values()) or 1
            shares = {k: round(minutes_per_day * s / total / 15) * 15 for k, s in scores.items()}
            shares = {k: m for k, m in shares.items() if m > 0} or {max(scores, key=scores.get): minutes_per_day}
        blocks = []
        for k, mins in sorted(shares.items(), key=lambda kv: -kv[1]):
            left = topics_left(data["exams"][k])
            topic = left[cursor[k] % len(left)] if left else "past papers"
            cursor[k] += 1
            blocks.append({"subject": data["exams"][k]["subject"], "minutes": mins, "topic": topic})
        plan.append({"date": day.isoformat(), "weekday": day.strftime("%A"), "blocks": blocks})

    first = plan[0]["blocks"]
    speech = (
        f"Here's your {len(plan)}-day plan at {minutes_per_day} minutes a day. Today: "
        + ", then ".join(f"{b['minutes']} minutes of {b['subject']} on {b['topic']}" for b in first)
        + ". I've sent the full timetable to your screen."
    )
    return {"speech": speech, "plan": plan}


@mcp.tool(
    title="Progress report",
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
)
def progress_report() -> dict[str, Any]:
    """Summarise every upcoming exam: days left, topics done, minutes studied."""
    data = load()
    rows = []
    for k in upcoming(data):
        e = data["exams"][k]
        done = sum(t["done"] for t in e["topics"])
        mins = sum(s["minutes"] for s in data["sessions"] if s["subject_key"] == k)
        rows.append(
            {"subject": e["subject"], "days_left": days_left(e), "topics_done": done,
             "topics_total": len(e["topics"]), "minutes_studied": mins}
        )
    if not rows:
        return {"speech": "Nothing to report yet. Add an exam first.", "exams": []}
    total_mins = sum(r["minutes_studied"] for r in rows)
    parts = [
        f"{r['subject']} {when(r['days_left'])}, {r['topics_done']} of {r['topics_total']} topics done"
        for r in rows
    ]
    h, m = divmod(total_mins, 60)
    studied = (f"{h} hour{'s' if h != 1 else ''} " if h else "") + (f"{m} minutes" if m or not h else "")
    speech = f"You've studied {studied.strip()} so far. " + ". ".join(parts) + "."
    return {"speech": speech, "exams": rows}


@mcp.tool(
    title="Remove an exam",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True),
)
def remove_exam(subject: str) -> dict[str, Any]:
    """Delete an exam and its study history (for example after the exam is over)."""
    with _lock:
        data = load()
        key = find_exam(data, subject)
        name = data["exams"].pop(key)["subject"]
        data["sessions"] = [s for s in data["sessions"] if s["subject_key"] != key]
        save(data)
    return {"speech": f"Removed {name}. Good luck with what's next!"}


# A read-only resource and a prompt, so clients can show/use the data directly.
@mcp.resource("studypilot://exams", title="Saved exams", mime_type="application/json")
def exams_resource() -> str:
    return json.dumps(load()["exams"], indent=2)


@mcp.prompt(title="Daily study check-in")
def daily_checkin(available_minutes: int = 60) -> str:
    return (
        f"I have {available_minutes} minutes today. Call progress_report, then what_should_i_study "
        f"with available_minutes={available_minutes}, and give me one short motivating sentence."
    )


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "server": "Study Pilot", "mcp_endpoint": "/mcp"})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Study Pilot MCP server")
    parser.add_argument("--reset", action="store_true", help="delete saved data before starting")
    args = parser.parse_args()
    if args.reset and DATA_FILE.exists():
        DATA_FILE.unlink()
        print("Saved data cleared.")
    print(f"Study Pilot MCP server -> http://{mcp.settings.host}:{mcp.settings.port}/mcp")
    mcp.run(transport="streamable-http")
