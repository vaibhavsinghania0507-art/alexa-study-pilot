"""
Simulated Alexa+ conversation against the real Study Pilot MCP server.

Alexa+ turns what you say into MCP tool calls. This script plays that role
with scripted turns, so you can see every request and spoken reply. The
server side is real: it speaks MCP 2025-11-25 over Streamable HTTP.

Run the server first (python server.py --reset), then:  python demo_alexa.py
Options: --fast (no pauses)   --url http://127.0.0.1:8000/mcp
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

if os.name == "nt":
    os.system("")  # turns on colour codes in the Windows terminal
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CYAN, YELLOW, GREY, GREEN, BOLD, END = "\033[96m", "\033[93m", "\033[90m", "\033[92m", "\033[1m", "\033[0m"

# (what the student says, tool Alexa+ picks, arguments it extracts)
CONVERSATION_1 = [
    ("Alexa, my Calculus exam is on 16 November. Topics are limits, derivatives, integration and series.",
     "add_exam", {"subject": "Calculus", "exam_date": "16 Nov",
                  "topics": ["Limits", "Derivatives", "Integration", "Series"]}),
    ("Add Python Programming on 19 November: loops, functions, OOP and file handling.",
     "add_exam", {"subject": "Python Programming", "exam_date": "19 Nov",
                  "topics": ["Loops", "Functions", "OOP", "File handling"]}),
    ("And Physics on 24 November, topics optics and quantum basics.",
     "add_exam", {"subject": "Physics", "exam_date": "24 Nov", "topics": ["Optics", "Quantum basics"]}),
    ("I just did 45 minutes of Calculus and finished limits.",
     "log_study_session", {"subject": "calculus", "minutes": 45, "topic": "Limits", "topic_finished": True}),
]

CONVERSATION_2 = [
    ("Alexa, I've got an hour free. What should I study?",
     "what_should_i_study", {"available_minutes": 60}),
    ("Make me a revision plan for the next 5 days, 2 hours a day.",
     "plan_revision", {"days": 5, "minutes_per_day": 120}),
    ("How am I doing overall?",
     "progress_report", {}),
]


def pause(fast: bool, seconds: float = 2.2) -> None:
    if not fast:
        time.sleep(seconds)


def show_plan(plan: list[dict]) -> None:
    for day in plan:
        blocks = " | ".join(f"{b['subject']} {b['minutes']}m ({b['topic']})" for b in day["blocks"])
        print(f"   {GREY}{day['weekday'][:3]} {day['date']}  {blocks}{END}")


async def run_conversation(url: str, title: str, turns: list, fast: bool) -> None:
    print(f"\n{BOLD}=== {title} ==={END}")
    async with streamablehttp_client(url) as (read, write, get_session_id):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print(f"{GREY}[MCP] connected to {init.serverInfo.name} | protocol {init.protocolVersion} "
                  f"| session {get_session_id()}{END}")
            for said, tool, args in turns:
                print(f"\n{YELLOW}YOU   >{END} {said}")
                pause(fast, 1.2)
                print(f"{GREY}[MCP] tools/call {tool} {json.dumps(args)}{END}")
                result = await session.call_tool(tool, args)
                data = result.structuredContent or {}
                if result.isError or not data:
                    text = " ".join(c.text for c in result.content if hasattr(c, "text"))
                    print(f"{CYAN}ALEXA >{END} {text}")
                else:
                    print(f"{CYAN}ALEXA >{END} {data.get('speech')}")
                    if tool == "plan_revision":
                        show_plan(data.get("plan", []))
                pause(fast)


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8000/mcp")
    p.add_argument("--fast", action="store_true")
    a = p.parse_args()

    print(f"{BOLD}{GREEN}Study Pilot for Alexa+ - simulated conversation{END}")
    async with streamablehttp_client(a.url) as (read, write, _):
        async with ClientSession(read, write) as s:
            await s.initialize()
            tools = await s.list_tools()
            print(f"{GREY}[MCP] tools/list -> {', '.join(t.name for t in tools.tools)}{END}")

    await run_conversation(a.url, "Conversation 1 (evening)", CONVERSATION_1, a.fast)
    print(f"\n{GREY}... conversation ends. New conversation, new MCP session. Nothing is re-told. ...{END}")
    pause(a.fast, 2.5)
    await run_conversation(a.url, "Conversation 2 (next morning)", CONVERSATION_2, a.fast)
    print(f"\n{GREEN}Done. Study Pilot remembered everything across sessions.{END}")


if __name__ == "__main__":
    asyncio.run(main())
