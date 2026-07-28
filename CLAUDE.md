# CLAUDE.md

## Project

**Recall** — a prototype shared-memory service for AI agents (FastAPI +
SQLite). Agents write durable organizational memories and retrieve them
later, with every read filtered by a scoping model (`team` is the default
floor, `global` is explicit opt-in earned through a promotion loop). See
`README.md` for the full design, API table, and demo flow.

Key layout:

- `memory_service/` — the service (auth, db, single enforced read path)
- `scripts/` — `seed.py` (simulated IdP), `add_agent.py` (simulated SSO
  agent registration), `create_demo_repos.py` (demo repo scaffolder),
  `demo.py` (five-act demo with leak assertions)
- `tests/` — 23 tests covering scoping, curation rights, promotion, auth
- `memory.db`, `.tokens.json` — local state; both stay out of git, and
  token values must never be printed or copied anywhere

Run tests with `.venv/bin/python -m pytest -q` (expect 23 passed). The
service runs at `http://127.0.0.1:8000` via
`.venv/bin/uvicorn memory_service.main:app --port 8000`.

## Demo deployment agent: `recall-demo-installer`

The demo is deployed by a Claude Code sub-agent defined at
`.claude/agents/recall-demo-installer.md`. Invoke it by asking to
*"set up the demo"* / *"install the demo"* / *"create the demo repos"*.

**What it does** — a complete cold-start installation of the demo
environment, in order:

1. Creates the Python virtualenv (Python 3.11+) and installs
   `requirements.txt`.
2. Runs the test suite and requires all 23 tests green — it never demos
   on a red suite.
3. Seeds the org directory (simulated IdP): teams `sase-gateway`,
   `sase-management`, `sase-platform`; users alice, bob (two teams), and
   carol. It deliberately does **not** register any agents.
4. Starts the memory service on port 8000 (falls back to another port on
   conflict) and verifies `/healthz`.
5. Scaffolds two git-initialized demo team repos via
   `scripts/create_demo_repos.py --dest ../demo-repos`:

   | Repo | Team | User | Suggested agent |
   |---|---|---|---|
   | `demo-repos/gateway-service` | sase-gateway | alice | gw-assistant |
   | `demo-repos/mgmt-portal` | sase-management | carol | mgmt-assistant |

   Each demo repo contains toy source, a `.recall/config.json` pointing
   back at this service, and the built-in `add-agent` skill
   (`.claude/skills/add-agent/SKILL.md`).
6. Reports what was installed and the human's next step: open a demo repo
   in Claude Code and run `/add-agent`.

**What it deliberately does not do:** register agents. Agent onboarding
is self-service through the `add-agent` skill inside each demo repo —
watching that skill ask which agent to create, register it with Recall,
and write a real sub-agent under `.claude/agents/` *is* the live demo.

**Safety rules it follows:** never write or print token values
(credentials live only in the gitignored, chmod-600 `.tokens.json`);
ask before deleting `memory.db`, `.tokens.json`, or an existing
`demo-repos/`; refuse rather than overwrite an existing scaffold
destination.

**Tools it uses:** Bash, Read, Write, Edit, Glob, Grep.
