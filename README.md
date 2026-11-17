# DevBox

A multi-agent LangChain assistant for your Windows PC (Groq or OpenAI), running in your browser.
It finds what is filling your disk, what is slowing your PC, what is holding a port, and which
dependencies are outdated or vulnerable. It shows you exact results and asks before changing anything.

## Run

```powershell
uv sync                      # creates .venv and installs everything
Copy-Item .env.example .env  # then put GROQ_API_KEY and/or OPENAI_API_KEY in .env
uv run devbox                # starts http://127.0.0.1:8765 and opens your browser
```

The browser opens with a one-time token in the URL. If you close the tab, use the link printed in the terminal.

## Screen

- **Main screen:** chat (center) + live **Agent activity** panel (right; toggle with *Activity*).
  The top bar shows the four agents with the active one highlighted.
- **Settings (⚙):**
  - *Model*: pick the provider (Groq / OpenAI) and any model your key can use (list loaded live
    from the provider), or type a model ID. Switching keeps the conversation; the choice is saved
    in `.devbox/model.json`. The current model also shows in the top bar.
  - *Usage & limits*: per provider, limits left from the provider's headers plus local counts,
    tokens, latency, 429s (live)
  - *Agents & tools*: which tools each agent has and their type
  - *Configuration*: model, limits, allowed / protected folders, log paths
  - *Action history*: every approved action
- **System details** only appear when you ask (e.g. "show my system details"). They come back as a
  card from `show_system_info`, with an optional **Live** toggle to keep refreshing.

## How handoff is applied

**1. Agent → agent** (LangChain "multiple agent subgraphs" pattern, `agents/graph.py`, `agents/handoff.py`)

```
START ─(active_agent)─► triage ──transfer_to_*──► storage | process | dependency
                                     ▲                     │
                                     └──── transfer_to_* ──┘
```

- Each agent is `create_agent(...)` with its own tools plus `transfer_to_<other>` tools.
- A handoff tool returns `Command(graph=Command.PARENT, goto="<agent>", update={...})`.
- `active_agent` is saved by the checkpointer, so your next message goes straight to the current specialist.
- Context engineering: the next agent gets a clean history (user messages, final answers, reports
  already shown) plus the handoff reason in its system prompt. It never sees another agent's tool calls.

**2. Agent → you**

| Type | Tools | Mechanism |
|---|---|---|
| Normal | `get_system_info`, `get_disk_usage`, `find_large_files`, `find_node_modules`, `list_running_processes`, `check_port_in_use`, `scan_outdated_packages`, `search_installed_programs` | result goes back to the LLM |
| A: show & stop | `show_system_info`, `show_storage_report`, `show_process_table`, `show_vulnerable_dependencies`, `show_git_repos_with_uncommitted_changes` | `@tool(return_direct=True)`: the agent stops and the exact report is rendered as a card |
| B: review & continue | `delete_files`, `kill_process`, `clear_cache`, `update_package`, `uninstall_program` | `interrupt(preview)` pauses the graph; the browser dialog resumes it with `Command(resume=...)` (approve / edit / reject) |

## Models and rate limits

`llm.py` builds the chat model (`ChatGroq` or `ChatOpenAI`) for the provider/model chosen in
Settings. Both send real rate-limit state in response headers, read through an httpx response hook
(`usage.record_headers`), but the request header means a different window:

| Header | Groq | OpenAI |
|---|---|---|
| `x-ratelimit-*-requests` | requests per **day** | requests per **minute** |
| `x-ratelimit-*-tokens` | tokens per **minute** | tokens per **minute** |
| `retry-after` (on 429) | seconds | seconds |

Values not in the headers are counted locally against `.env` (Groq: `GROQ_RPM_LIMIT`,
`GROQ_TPD_LIMIT`; OpenAI: optional `OPENAI_RPD_LIMIT`). Before each call DevBox waits if the
provider said `retry-after`, if requests per minute are used up, or if the tokens left this minute
are too low (⏳ in the activity panel). Limits apply to your whole Groq / OpenAI organization.

## Security

DevBox can delete files and kill processes, so the web server is locked down:

- Binds to `127.0.0.1` only (never reachable from your network).
- Every API call and the WebSocket need a random token generated at each launch.
- `Host` and `Origin` must be `127.0.0.1` / `localhost` (blocks other websites and DNS rebinding).
- Strict Content-Security-Policy; all tool output is inserted as text, never as HTML.

## Safety

- Deletes go to the **Recycle Bin** (`send2trash`).
- Only folders in `DEVBOX_ALLOWED_ROOTS` (default Desktop, Downloads, Documents) can be scanned or changed.
- Windows, Program Files, ProgramData, the allowed roots themselves and DevBox's own folder can't be deleted.
- System processes (csrss, svchost, explorer, ...), DevBox and its parent terminal can't be killed.
- Every approved action is written to `logs/devbox_actions.log`.
- Run as a normal user, not as admin.
- `show_vulnerable_dependencies` sends package names and versions to PyPI/OSV (pip-audit) and to npm (npm audit).

## Layout

```
src/devbox/
├── server.py           FastAPI app: static UI, REST (/api/*), WebSocket (/ws), auth
├── static/             index.html · app.css · app.js (no build step)
├── engine.py           runs a turn, loops over interrupts
├── callbacks.py        LLM/tool events → activity panel + usage tracker
├── llm.py              Groq / OpenAI models, selected model, live model list
├── usage.py            per-provider rate-limit tracker (headers) + throttle
├── system_report.py    on-demand system details
├── config.py           .env settings, allowed/protected paths
├── safety.py           path + process guards, action log
├── agents/             graph.py · handoff.py · prompts.py
└── tools/              normal.py · show.py (A) · actions.py (B)
```
