# Recall — Scoped Agent Memory Service (Prototype)

A shared-memory layer for AI agents: agents **write** durable memories,
**retrieve** them in later sessions or from other agents, and every read is
filtered by a **scoping model** — `team` (the floor, default) and `global`
(explicit opt-in, primarily earned through a promotion loop) — so no agent
sees memory it shouldn't.

## The problem I chose to solve, and why

Shared agent memory spans many problems: session context, cross-agent state,
long-term knowledge, user preferences. I scoped this prototype to
**durable, scoped organizational knowledge for developer-facing agents** —
specifically *decisions, conventions, insights, and lessons* that agents
produce or consume while working (code review agents, on-call agents, CI
agents).

Why this slice first:

1. **It's where the compounding value is.** Session context helps one agent
   once; a recorded decision helps every agent, forever. It directly attacks
   the stated pain: *"no organizational knowledge that accumulates over time."*
2. **It's the highest-leverage shared primitive.** Every team building an
   agent needs write/retrieve/scope. Shipping it once as a small service
   means teams stop rebuilding it from scratch.
3. **It forces the hard design question early: access control.** In a
   security company, a memory layer without an access model is a
   non-starter. The enforcement point cannot be retrofitted — so it was
   built first, and it survived a full design evolution (below) unchanged.
4. **It's demonstrable in minutes without external dependencies** — no
   vector DB, no API keys — while keeping a clear upgrade path to hybrid
   semantic retrieval.

## The model (v1 — evolved through security review)

```
scope     ∈  team (default, the floor) | global (explicit opt-in)
identity  =  user (permissions — team memberships) + agent (provenance)
predicate:   scope = 'global' OR team_id IN (user's teams)
curation:    any member of the owning team retires team memories;
             global memories are demoted via review, never deleted
promotion:   global is EARNED — blocked cross-team searches become demand
             signals; the digest names demand teams; the owning team
             consents to a generalized rewrite (derived-from + promoted-by)
```

The first version used three tiers and per-agent identity; security review
simplified it to the model above. **The enforcement point — one visibility
predicate inside the single read path — never moved**; only identity
resolution and the predicate changed (`auth.py` / `db.py`). That is the
architecture's core claim, demonstrated on a real design change.

Identity in the prototype **simulates** the production flow: production
uses an SSO device flow with short-lived tokens held in the session
environment/keychain; the prototype mints local demo tokens
(`scripts/add_agent.py`), stores only SHA-256 hashes, and writes plaintext
to a gitignored, chmod-600 `.tokens.json` as a stand-in for the keychain.

## Run it locally

Requires Python 3.11+.

```bash
cd repo
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 1. Seed the org directory (simulates the IdP: teams, users, memberships)
python scripts/seed.py

# 2. Register agents — simulates the self-service add-agent skill.
#    Note bob belongs to TWO teams, and the on-call agent has a
#    relevance profile that shapes its session bootstrap.
python scripts/add_agent.py --user alice --agent gw-code-reviewer \
    --name "Gateway Code Review Agent"
python scripts/add_agent.py --user bob --agent gw-oncall-helper \
    --name "Gateway On-call Agent" \
    --tags "retries incidents upstream vendor" --kinds decision,lesson
python scripts/add_agent.py --user carol --agent mgmt-code-reviewer \
    --name "Management Code Review Agent"

# 3. Start the service
uvicorn memory_service.main:app --port 8000
```

In a second terminal (same venv):

```bash
# 4. The demo flow — five acts, with assertions that crash on any leak
python scripts/demo.py
```

### The demo flow

| Act | What it proves |
|---|---|
| 1 · REMEMBER | An agent stores a team decision and a lesson as a side effect of work |
| 2 · RECALL | A *different user's* agent, new session: profile-shaped bootstrap + search retrieval; a two-team user sees both teams |
| 3 · THE WALLS | Another team's searches return nothing; direct fetch → 404 (existence doesn't leak); its blocked searches become demand signals |
| 4 · THE LOOP | The promotion digest surfaces the lesson with the demand team **named** (content never shown); the owner promotes a generalized rewrite; the demand team can now retrieve it |
| 5 · CONTROL | Non-owners can't demote or delete; the owning team demotes and the wall closes again |

Run the test suite (scoping, multi-team visibility, curation rights,
promotion loop, retrieval, auth, input validation):

```bash
pytest        # 23 passed
```

Interactive API docs: http://127.0.0.1:8000/docs (authorize with a value
from `.tokens.json`).

## The demo installer sub-agent

The repo ships a Claude Code sub-agent —
`.claude/agents/recall-demo-installer.md` — that performs the whole
installation from a cold start: dependencies, test verification, seeded
directory, running service, and **two scaffolded demo team repositories**.
Open this repo in Claude Code and ask it to *"set up the demo"*.

The scaffolder it uses is deterministic and can be run directly:

```bash
python scripts/create_demo_repos.py --dest ../demo-repos
```

| Demo repo | Team | User | Suggested agent |
|---|---|---|---|
| `demo-repos/gateway-service` | sase-gateway | alice | gw-assistant |
| `demo-repos/mgmt-portal` | sase-management | carol | mgmt-assistant |

Each demo repo is a small git-initialized project with the **`add-agent`
skill built in** (`.claude/skills/add-agent/SKILL.md`) and a
`.recall/config.json` pointing back at this service. Opening a demo repo
in Claude Code and running `/add-agent` performs the self-service
onboarding from doc 07: asks which agent to create, registers it
(simulated SSO), and writes a real Claude Code sub-agent
(`.claude/agents/<id>.md`) whose prompt carries the runtime loop
(bootstrap → search → distill-and-write) with call-time credential
lookups (token values are never written into the demo repo), then
smoke-tests the connection. Watching the
skill do this — then watching the two repos' agents share knowledge
through the promotion loop — is the live demo.

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/memories` | Write (`content`, `scope`, `kind`, `tags`, `team` if multi-team) |
| POST | `/v1/memories/search` | BM25 search over visible memories; records cross-team demand |
| GET | `/v1/memories/recent` | Recent visible memories |
| GET | `/v1/memories/bootstrap` | Profile-shaped session bootstrap |
| GET | `/v1/memories/{id}` | Fetch one if visible (404 otherwise — no existence leaks) |
| DELETE | `/v1/memories/{id}` | Retire — any member of the owning team (409 for global) |
| POST | `/v1/memories/{id}/promote` | Team → global: generalized rewrite, derived-from + promoted-by |
| POST | `/v1/memories/{id}/demote` | Global → team (the review path) — owning team only |
| GET | `/v1/suggestions` | The promotion digest: candidates + blocked counts + demand teams |
| GET | `/v1/whoami` | Principal identity (user, agent, teams) |

## Limitations / path to production

Honest list — this is a prototype:

- **Retrieval:** BM25 keyword search, not semantic. Production: hybrid
  retrieval (BM25 + embeddings in pgvector/OpenSearch) behind the same API,
  measured against an eval set harvested from real queries.
- **Storage:** single-file SQLite. Production: Postgres, encryption at
  rest (AES-256), backups, migrations.
- **Identity:** local demo tokens simulating SSO. Production: OIDC device
  flow, short-lived session-scoped tokens (keychain/env only — never
  files), full audit log of both user and agent claims.
- **Promotion loop:** computed on demand from blocked-hit signals.
  Production: a weekly batch inside the service boundary with usefulness
  signals, recurrence clustering across teams, and co-owned (multi-source)
  candidates requiring consent from every source team.
- **Memory lifecycle:** no TTL/decay, supersedes-edges, dedup, or conflict
  surfacing yet — the hardest unsolved problems in the space; see the
  documentation dossier for how the design approaches them.
