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

## The demo installer sub-agent

The repo ships a Claude Code sub-agent —
`.claude/agents/recall-demo-installer.md` — that performs the whole
installation from a cold start: dependencies, test verification, seeded
directory, running service, and **two scaffolded demo team repositories**.
Open this repo in Claude Code and ask it to *"set up the demo"*.

After the installer finishes, ask Claude to **run the UI**, then open the
demo runbook at **http://localhost:8000/ui/runbook** and follow the
instructions there — it walks you through the live demo step by step
(the console itself lives at http://localhost:8000/ui).
