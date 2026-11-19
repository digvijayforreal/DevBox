"""Temporary one-off script: fill the internship work log for 26 Oct - 19 Nov 2026 from POD/EOD notes.

Only the two worksheet XML parts are edited; every other part of the .xlsx is copied unchanged.
The original workbook is left untouched and the result is written to a new dated file.
"""
import re
import zipfile
from datetime import date, datetime, timedelta
from html import unescape
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

SRC = Path(r"C:\Users\user\Desktop\Internshi report\AI_ML_Internship_Daily_Work_Log_Updated_16_Oct_2026.xlsx")
DST = SRC.with_name("AI_ML_Internship_Daily_Work_Log_Updated_19_Nov_2026.xlsx")
LOG_PART = "xl/worksheets/sheet1.xml"  # "Daily Work Log"
OVERVIEW_PART = "xl/worksheets/sheet2.xml"  # "Overview"

EXCEL_EPOCH = date(1899, 12, 30)
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
COL_WIDTHS = {"C": 27, "D": 78, "E": 40, "F": 38}  # widths of the wrapped text columns in <x:cols>
LINE_PT, PAD_PT, MIN_ROW_PT = 15, 9, 27  # 11pt line height, cell padding, empty-row height

# date: (Project / Module, Work Completed / Tasks, Tools & Technologies, Outcome / Deliverable, Status)
ENTRIES = {
    date(2026, 10, 26): (
        "LangGraph Multi-Agent AI / ReAct Workflow & Tools",
        "Developed a separate ReAct workflow without modifying the existing LangGraph workflow. "
        "Integrated four tools: Tavily web search, PDF generation, date/time utilities and email sending. "
        "Tested direct email sending through SMTP.",
        "Python; LangGraph; LangChain; ReAct Agent; Tavily Web Search; PDF Generation; SMTP",
        "Separate ReAct workflow with four integrated tools; SMTP email sending tested",
        "Completed",
    ),
    date(2026, 10, 27): (
        "LangGraph Multi-Agent AI / ReAct Workflow Testing",
        "Verified the frontend workflow switch for selecting between the existing LangGraph workflow and the "
        "new ReAct workflow, and tested the complete ReAct execution flow. Kept the ReAct workflow separate so "
        "the existing LangGraph workflow stayed unchanged.",
        "Python; LangGraph; LangChain; ReAct Agent; Frontend",
        "Workflow switch verified and full ReAct flow tested; existing LangGraph workflow unchanged",
        "Completed",
    ),
    date(2026, 10, 28): (
        "Leave",
        "On leave for passport work — no internship tasks.",
        "",
        "On Leave",
        "On Leave",
    ),
    date(2026, 10, 29): (
        "Rate Limiting API / FastAPI & Redis",
        "Implemented a Rate Limiting API using FastAPI and Redis. Added Fixed Window and Token Bucket "
        "rate-limiting algorithms, and implemented authentication, concurrency control and rate-limit headers.",
        "Python; FastAPI; Redis; Fixed Window; Token Bucket",
        "Rate Limiting API with Fixed Window and Token Bucket algorithms, authentication, concurrency control "
        "and rate-limit headers",
        "Completed",
    ),
    date(2026, 10, 30): (
        "Rate Limiting API / Test Submission",
        "Completed the Rate Limiting API test and submitted it (Rate-Limit-API). "
        "Next assigned task: adding campaigns to the new project.",
        "Python; FastAPI; Redis",
        "Rate-Limit-API test submitted",
        "Completed",
    ),
    date(2026, 11, 2): (
        "iHAT Netcore CF Campaigns / CF Simulator",
        "Started on the iHAT campaigns: reviewed the ihat-netcore project and its campaign flows. Built an "
        "offline HTTP/Swagger CF simulator that drives the real flows node by node and returns JSON. "
        "Learned about Bitbucket and GitHub Actions.",
        "Python; Netcore; HTTP; Swagger UI; JSON; Bitbucket; GitHub Actions",
        "Offline CF simulator returning JSON for node-by-node flow runs; campaign flows reviewed",
        "Completed",
    ),
    date(2026, 11, 3): (
        "iHAT Netcore CF Campaigns / Female Flow & Reminders",
        "Resumed work on the Netcore-iHAT CF campaign and updated the message content in the female flow and "
        "reminder files. Pushed the changes and merged them into the repository.",
        "Python; Netcore; Git",
        "Female flow and reminder message updates pushed and merged",
        "Completed",
    ),
    date(2026, 11, 4): (
        "iHAT Netcore CF Campaigns / Female Flow Testing & Male CF Fixes",
        "Tested the iHAT CF female campaign end-to-end, verifying messages and node triggers across the "
        "complete workflow. Fixed issues in the male CF codebase, including function signature mismatches, "
        "syntax errors, and missing buttons and links. Pushed the updated changes to the remote branch.",
        "Python; Netcore; Git",
        "Female campaign verified end-to-end; male CF fixes pushed to the remote branch",
        "Completed",
    ),
    date(2026, 11, 5): (
        "iHAT Netcore CF Campaigns / Message & Webhook Fixes",
        "Continued end-to-end verification of the iHAT CF female campaign; manually tested campaign messages, "
        "reply buttons and follow-up messages. Updated handler.py and message_webhook.py to handle menu "
        "selections and fixed missing video links. Checked message formatting and removed test text.",
        "Python; Netcore; Webhooks",
        "Menu selections handled, video links fixed and message formatting cleaned up",
        "Completed",
    ),
    date(2026, 11, 9): (
        "iHAT Netcore CF Campaigns / Female Workflow Testing",
        "Tested the iHAT CF female campaign end-to-end, verifying messages and node triggers across the "
        "complete workflow, including female workflows 7 (C3, C4), 8 and 9.",
        "Python; Netcore",
        "Female workflows 7 (C3, C4), 8 and 9 tested",
        "Completed",
    ),
    # POD only (no EOD was posted), so it is worded as work carried out and marked In Progress.
    date(2026, 11, 11): (
        "iHAT Netcore CF Campaigns / Reminder Flow Testing",
        "Worked on testing the iHAT CF female campaign reminder message flow and node triggers, and on "
        "identifying and resolving code and syntax issues.",
        "Python; Netcore",
        "Reminder flow and node-trigger testing underway",
        "In Progress",
    ),
    date(2026, 11, 12): (
        "iHAT Netcore CF Campaigns / Reminder Scheduler",
        "Implemented a reminder/follow-up scheduler for the iHAT CF campaigns. Updated the URLs in the female "
        "flows and tested the reminders via the scheduler.",
        "Python; Netcore; Scheduler",
        "Reminder/follow-up scheduler implemented; female flows updated and reminders tested",
        "Completed",
    ),
    date(2026, 11, 13): (
        "iHAT CF Scheduler / Job Queue & Task Scheduling Assignment",
        "Tested the iHAT CF campaign reminder flow via the scheduler. Completed the internship assignment on "
        "Job Queue and Task Scheduling.",
        "Python; Netcore; Scheduler; Job Queue",
        "CF reminder flow tested via the scheduler; Job Queue & Task Scheduling assignment completed",
        "Completed",
    ),
    date(2026, 11, 16): (
        "Multi-Agent Trip Planner / LangGraph Supervisor",
        "Learned LangGraph and designed a supervisor-led multi-agent workflow for trip planning. Developed the "
        "backend with FastAPI and Groq, and a glassmorphism chat UI with saved chat history. "
        "Tested the output quality.",
        "Python; LangGraph; FastAPI; Groq",
        "Supervisor-led trip-planning assistant with FastAPI + Groq backend and chat UI with saved history",
        "Completed",
    ),
    date(2026, 11, 17): (
        "DevBox Multi-Agent Assistant / LangChain Handoffs",
        "Built a multi-agent AI assistant for Windows using LangChain handoffs, where a triage agent routes "
        "requests to the storage, process and dependency agents. Added Groq and OpenAI support with "
        "switchable models, rate-limit tracking and LangSmith tracing.",
        "Python; LangChain; LangGraph; Groq; OpenAI; LangSmith",
        "Multi-agent Windows assistant with triage routing, switchable Groq/OpenAI models, rate-limit tracking "
        "and LangSmith tracing",
        "Completed",
    ),
    date(2026, 11, 18): (
        "DevBox Multi-Agent Assistant / Handoffs & return_direct",
        "Set up LangChain handoffs from the triage agent to the storage, process and dependency agents with "
        "transfer_to_* tools. Applied return_direct=True so selected tools send their results straight to the "
        "user, and tested the full flow.",
        "Python; LangChain; LangGraph; transfer_to_* Tools; return_direct",
        "Working triage handoffs and return_direct tools; full flow tested",
        "Completed",
    ),
    # Today: POD only so far, the EOD is not posted yet.
    date(2026, 11, 19): (
        "DevBox Multi-Agent Assistant / LangGraph System Agents",
        "Adding the system agents to the LangGraph graph as new nodes with their normal and medium-risk tools, "
        "connected to triage through transfer_to_* handoffs. Testing the answers and the full workflow: "
        "handoffs, the approval flow and return_direct tools sending results straight to the user. "
        "Organizing the project files with a separate folder for each agent and its tools.",
        "Python; LangGraph; LangChain; transfer_to_* Handoffs; Approval Flow",
        "In progress: system agents in the LangGraph graph, full workflow testing and per-agent folder structure",
        "In Progress",
    ),
}

OVERVIEW_LATEST_DATE = "19 Nov 2026"
OVERVIEW_LATEST = {
    "B12": "DevBox Multi-Agent Assistant / LangGraph System Agents",
    "B13": "Adding the system agents to the LangGraph graph as new nodes with their normal and medium-risk tools.",
    "B14": "Routing: triage hands requests to each agent through transfer_to_* handoff tools.",
    "B15": "Organizing the project with a separate folder for each agent and its tools.",
    "B16": "Testing answers, handoffs, the approval flow and return_direct tools (in progress).",
}
OVERVIEW_WEEK_TITLE = "This Week (16–20 Nov)"
OVERVIEW_WEEK = [
    ("16 Nov entry", "Supervisor-led trip-planning multi-agent workflow (FastAPI + Groq) with chat UI"),
    ("17 Nov entry", "Windows multi-agent assistant with LangChain handoffs, Groq/OpenAI models and LangSmith tracing"),
    ("18 Nov entry", "transfer_to_* handoffs and return_direct tools set up; full flow tested"),
    ("19 Nov entry", "System agents being added to the LangGraph graph (in progress)"),
]
EXPECTED_LOGGED, EXPECTED_COMPLETED = 23, 21  # 7 + 16 logged days; 7 + 14 completed


def find_row(xml: str, r: int) -> re.Match:
    m = re.search(rf'<x:row r="{r}"[ >].*?</x:row>', xml, re.S)
    if not m:
        raise KeyError(f"row {r} not found")
    return m


def find_cell(xml: str, ref: str) -> re.Match:
    m = re.search(rf'<x:c r="{ref}"(?P<attrs>[^>]*?)(?:/>|>(?P<body>.*?)</x:c>)', xml, re.S)
    if not m:
        raise KeyError(f"cell {ref} not found")
    return m


def cell_text(xml: str, ref: str) -> str:
    body = find_cell(xml, ref).group("body") or ""
    v = re.search(r"<x:v>(.*?)</x:v>", body, re.S)
    return unescape(v.group(1)) if v else ""


def set_cell(xml: str, ref: str, value) -> str:
    """Write a string or number into an existing cell, keeping its style index."""
    m = find_cell(xml, ref)
    style = re.search(r'\ss="(\d+)"', m.group("attrs"))
    s_attr = f' s="{style.group(1)}"' if style else ""
    if isinstance(value, int):
        new = f'<x:c r="{ref}"{s_attr} t="n"><x:v>{value}</x:v></x:c>'
    else:
        new = f'<x:c r="{ref}"{s_attr} t="str"><x:v>{escape(value)}</x:v></x:c>'
    return xml[: m.start()] + new + xml[m.end():]


def set_row_height(row_xml: str, ht: int) -> str:
    out, n = re.subn(r'^(<x:row r="\d+") ht="[^"]*"', rf'\g<1> ht="{ht}"', row_xml, count=1)
    if n != 1:
        raise ValueError("row height attribute not found")
    return out


def wrapped_lines(text: str, width: float) -> int:
    cpl = max(1, int(width * 1.08))  # conservative chars-per-line so text is not clipped
    lines, used = 1, 0
    for word in text.split():
        if used and used + 1 + len(word) <= cpl:
            used += 1 + len(word)
            continue
        if used:
            lines += 1
        lines += (len(word) - 1) // cpl
        used = (len(word) - 1) % cpl + 1
    return lines


def needed_height(texts: dict) -> int:
    lines = max((wrapped_lines(t, COL_WIDTHS[c]) for c, t in texts.items() if t), default=1)
    return max(MIN_ROW_PT, lines * LINE_PT + PAD_PT)


def update_log(xml: str):
    applied, logged, completed = set(), 0, 0
    for r in range(5, 67):
        m = find_row(xml, r)
        row = m.group(0)
        d = EXCEL_EPOCH + timedelta(days=int(cell_text(row, f"A{r}")))
        if cell_text(row, f"B{r}") != DAY_NAMES[d.weekday()]:
            raise ValueError(f"row {r}: day name does not match {d}")
        if d in ENTRIES:
            existing = [cell_text(row, f"{c}{r}") for c in "CDEFG"]
            if any(existing):
                raise ValueError(f"row {r} ({d}) already has content: {existing}")
            project, work, tools, outcome, status = ENTRIES[d]
            texts = {"C": project, "D": work, "E": tools, "F": outcome}
            for col, text in {**texts, "G": status}.items():
                if text:
                    row = set_cell(row, f"{col}{r}", text)
            ht = needed_height(texts)
            row = set_row_height(row, ht)
            applied.add(d)
            print(f"  row {r:>2} {d:%a %d %b}  ht={ht:<4} {status:<12} {project}")
        else:
            # Existing rows: only grow heights that clearly clip their text (2+ lines short).
            texts = {c: cell_text(row, f"{c}{r}") for c in COL_WIDTHS}
            if any(texts.values()):
                cur = float(re.search(r'ht="([^"]+)"', row).group(1))
                need = needed_height(texts)
                if need - cur >= 2 * LINE_PT:
                    row = set_row_height(row, need)
                    print(f"  row {r:>2} {d:%a %d %b}  ht {cur:g} -> {need} (existing text was clipped)")
        xml = xml[: m.start()] + row + xml[m.end():]
        status = cell_text(row, f"G{r}")
        if cell_text(row, f"D{r}") and status not in ("Holiday", "On Leave"):
            logged += 1
        completed += status == "Completed"
    missing = set(ENTRIES) - applied
    if missing:
        raise ValueError(f"entries not applied: {sorted(missing)}")
    return xml, logged, completed


def update_overview(xml: str, logged: int, completed: int) -> str:
    xml = set_cell(xml, "B7", logged)
    xml = set_cell(xml, "B8", completed)
    xml = set_cell(xml, "A11", f"{OVERVIEW_LATEST_DATE} • Latest Logged Work Summary")
    for ref, text in OVERVIEW_LATEST.items():
        xml = set_cell(xml, ref, text)
    xml = set_cell(xml, "A18", OVERVIEW_WEEK_TITLE)
    # Rows 19-20 already exist with this layout; rows 21-22 are appended right after row 20.
    for i, (label, text) in enumerate(OVERVIEW_WEEK[:2], start=19):
        xml = set_cell(xml, f"A{i}", label)
        xml = set_cell(xml, f"B{i}", text)
    if '<x:row r="21"' in xml:
        raise ValueError("Overview already has a row 21")
    end = find_row(xml, 20).end()
    extra = "".join(
        f'<x:row r="{i}"><x:c r="A{i}" t="str"><x:v>{escape(label)}</x:v></x:c>'
        f'<x:c r="B{i}" s="24" t="str"><x:v>{escape(text)}</x:v></x:c></x:row>'
        for i, (label, text) in enumerate(OVERVIEW_WEEK[2:], start=21)
    )
    return xml[:end] + extra + xml[end:]


def main() -> None:
    if SRC == DST:
        raise ValueError("refusing to overwrite the original workbook")
    with zipfile.ZipFile(SRC) as zin:
        infos = zin.infolist()
        parts = {i.filename: zin.read(i.filename) for i in infos}

    print("Daily Work Log:")
    log, logged, completed = update_log(parts[LOG_PART].decode("utf-8"))
    print(f"Overview: logged workdays={logged}, completed entries={completed}")
    if (logged, completed) != (EXPECTED_LOGGED, EXPECTED_COMPLETED):
        raise ValueError("overview counts differ from the expected values")
    overview = update_overview(parts[OVERVIEW_PART].decode("utf-8"), logged, completed)
    parts[LOG_PART] = log.encode("utf-8")
    parts[OVERVIEW_PART] = overview.encode("utf-8")

    now = datetime.now().timetuple()[:6]
    with zipfile.ZipFile(DST, "w") as zout:
        for info in infos:
            changed = info.filename in (LOG_PART, OVERVIEW_PART)
            zi = zipfile.ZipInfo(info.filename, date_time=now if changed else info.date_time)
            zi.compress_type = info.compress_type
            zi.external_attr = info.external_attr
            zout.writestr(zi, parts[info.filename])

    # Verify archive integrity and that every part is well-formed XML.
    with zipfile.ZipFile(DST) as z:
        if z.testzip() is not None:
            raise ValueError("corrupt zip entry")
        for name in z.namelist():
            ET.fromstring(z.read(name))
    print(f"Wrote {DST}")


if __name__ == "__main__":
    main()
