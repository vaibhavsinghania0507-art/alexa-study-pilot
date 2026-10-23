# Study Pilot — an Alexa+ study planner that remembers

**Track:** Alexa+ (self-hosted MCP server) · **MCP spec:** 2025-11-25 · **Transport:** Streamable HTTP

Students tell Alexa about their exams once. Study Pilot remembers the exams, syllabus topics and every study session **across conversations**, and answers the question students actually ask every day: *"I have an hour — what should I study?"*

## What it does

| Tool | What you'd say to Alexa+ | What happens |
|---|---|---|
| `add_exam` | "My Calculus exam is on 16 November: limits, derivatives…" | Saves the exam and its topics |
| `log_study_session` | "I did 45 minutes of Calculus and finished limits" | Logs time, ticks off the topic |
| `what_should_i_study` | "I've got an hour, what should I study?" | Picks the most urgent subject + topic |
| `plan_revision` | "Plan the next 5 days, 2 hours a day" | Day-by-day timetable in 15-min blocks |
| `progress_report` | "How am I doing?" | Days left, topics done, minutes studied |
| `remove_exam` | "Remove Physics, the exam's over" | Deletes it (marked `destructiveHint`) |

Every tool returns **structured output** plus a short `speech` field written to be read aloud.
Also exposes a resource (`studypilot://exams`) and a prompt (`daily_checkin`).

### How "what should I study" decides

```
urgency = (topics left + 1) / (days left + 1) / (1 + minutes studied in last 3 days / 60)
```

More topics left and fewer days left push a subject up; studying it recently pushes it down, so subjects rotate. In `plan_revision`, the day before an exam is reserved entirely for that subject.

## Run it (Python 3.10+)

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

python server.py --reset        # server at http://127.0.0.1:8000/mcp
```

In a second terminal (same venv):

```bash
python demo_alexa.py            # simulated Alexa+ conversation against the real server
```

Health check: `http://127.0.0.1:8000/health`. Change host/port with the `HOST` and `PORT` environment variables. Data is stored in `data/study_data.json` (override with `STUDY_PILOT_DATA`).

### Connect any MCP client

Point a Streamable HTTP MCP client at `http://127.0.0.1:8000/mcp`. For example, with MCP Inspector:

```bash
npx @modelcontextprotocol/inspector
# Transport: Streamable HTTP, URL: http://127.0.0.1:8000/mcp
```

## About the demo

Alexa+ turns speech into MCP tool calls. `demo_alexa.py` plays that orchestrator role with scripted turns so judges can see each `tools/call` and the spoken reply. The server is real: it negotiates protocol `2025-11-25` and the second conversation opens a **new MCP session** to show that memory survives across sessions.

## Files

- `server.py` — the MCP server (tools, resource, prompt, health route, JSON storage)
- `demo_alexa.py` — simulated Alexa+ conversation using the official MCP Python client
- `requirements.txt` — pinned `mcp[cli]==1.30.0`

## License

MIT
