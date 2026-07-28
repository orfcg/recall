"""The demo flow — five acts, matching the presentation story.

Prerequisites:
  1. python scripts/seed.py
  2. python scripts/add_agent.py --user alice --agent gw-code-reviewer \
        --name "Gateway Code Review Agent"
     python scripts/add_agent.py --user bob --agent gw-oncall-helper \
        --name "Gateway On-call Agent" \
        --tags "retries incidents upstream vendor" --kinds decision,lesson
     python scripts/add_agent.py --user carol --agent mgmt-code-reviewer \
        --name "Management Code Review Agent"
  3. uvicorn memory_service.main:app --port 8000   (separate terminal)
  4. python scripts/demo.py

Acts:
  1. REMEMBER  — alice's review agent stores a team decision and a lesson
  2. RECALL    — bob's on-call agent (different user+agent, new session)
                 bootstraps from its profile and finds the decision;
                 bob's multi-team visibility is shown
  3. THE WALLS — carol's management agent searches the same topics:
                 nothing. Direct fetch: 404. Her blocked searches become
                 demand signals.
  4. THE LOOP  — alice opens the promotion digest: the lesson is a
                 candidate, with sase-management NAMED as a demand team.
                 She promotes a generalized version → carol's agent can
                 now learn from it.
  5. CONTROL   — carol cannot demote the gateway team's global memory;
                 alice can, and the walls close again.

Every act ends in assertions — the demo crashes loudly on any leak.
"""

import json
import sys
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000"
TOKENS_FILE = Path(__file__).resolve().parents[1] / ".tokens.json"


def client(agent_id: str, tokens: dict) -> httpx.Client:
    if agent_id not in tokens:
        sys.exit(f"no credentials for '{agent_id}' — run the add_agent "
                 "commands from the docstring first")
    return httpx.Client(base_url=BASE,
                        headers={"Authorization": f"Bearer {tokens[agent_id]}"},
                        timeout=10)


def show(title, results):
    print(f"\n  --- {title} ---")
    if not results:
        print("      (no results)")
    for r in results:
        print(f"      [{r['scope']:^6}] ({r['kind']}) {r['team_name']} · "
              f"{r['agent_id']} · {r['content'][:70]}")


def act(n, title):
    print(f"\n{'=' * 62}\n  ACT {n} — {title}\n{'=' * 62}")


def main() -> None:
    if not TOKENS_FILE.exists():
        sys.exit("Run scripts/seed.py and the add_agent commands first.")
    tokens = json.loads(TOKENS_FILE.read_text())
    reviewer = client("gw-code-reviewer", tokens)   # alice · sase-gateway
    oncall = client("gw-oncall-helper", tokens)     # bob   · gateway+platform
    mgmt = client("mgmt-code-reviewer", tokens)     # carol · sase-management

    for c, label in [(reviewer, "reviewer"), (oncall, "oncall"), (mgmt, "mgmt")]:
        me = c.get("/v1/whoami").json()
        print(f"{label:>9}: {me['agent_id']} — user {me['user_id']} "
              f"— teams: {', '.join(me['teams'])}")

    # ── ACT 1 · REMEMBER ────────────────────────────────────────────
    act(1, "REMEMBER — knowledge is written as a side effect of work")
    r = reviewer.post("/v1/memories", json={
        "content": "Decision: gateway retries upstream calls with "
                   "exponential backoff, max 3 attempts, jitter enabled — "
                   "chosen over circuit breaker for v1 simplicity.",
        "scope": "team", "kind": "decision", "tags": "retries backoff upstream"})
    r.raise_for_status()
    decision_id = r.json()["id"]
    print(f"  team decision stored (id={decision_id}, "
          f"team={r.json()['team_name']})")

    r = reviewer.post("/v1/memories", json={
        "content": "Lesson: vendor X silently rate-limits at 100 rps — "
                   "batch outbound calls or requests vanish without errors.",
        "scope": "team", "kind": "lesson", "tags": "vendor rate-limit batching"})
    r.raise_for_status()
    lesson_id = r.json()["id"]
    print(f"  team lesson stored   (id={lesson_id})")

    # ── ACT 2 · RECALL ──────────────────────────────────────────────
    act(2, "RECALL — a different user's agent benefits, cross-session")
    boot = oncall.get("/v1/memories/bootstrap").json()
    show("bootstrap read (profile-shaped: retries/incidents, "
         "decision+lesson kinds)", boot)
    assert any("backoff" in m["content"] for m in boot), \
        "bootstrap should surface the retry decision"

    found = oncall.post("/v1/memories/search",
                        json={"query": "how do we handle upstream retries"}).json()
    show("on-call agent searches 'upstream retries'", found)
    assert any(m["id"] == decision_id for m in found), \
        "cross-agent recall failed"

    r = oncall.post("/v1/memories", json={
        "content": "Note: platform CI runners recycle hourly; long jobs "
                   "must checkpoint.", "scope": "team", "kind": "note",
        "team": "sase-platform", "tags": "ci runners"})
    r.raise_for_status()
    print(f"\n  bob (two teams) wrote to sase-platform explicitly "
          f"(id={r.json()['id']})")
    recent = oncall.get("/v1/memories/recent").json()
    teams_seen = {m["team_name"] for m in recent}
    assert {"sase-gateway", "sase-platform"} <= teams_seen, \
        "multi-team visibility failed"
    print(f"  bob's recent view spans his teams: {sorted(teams_seen)}")

    # ── ACT 3 · THE WALLS ───────────────────────────────────────────
    act(3, "THE WALLS — another team sees nothing, learns nothing")
    for q in ["vendor rate limit batching", "vendor calls vanish",
              "upstream retries backoff"]:
        res = mgmt.post("/v1/memories/search", json={"query": q}).json()
        gateway_leaks = [m for m in res if m["team_name"] == "sase-gateway"]
        assert not gateway_leaks, f"SCOPING VIOLATION on query: {q}"
        print(f"  mgmt search {q!r:<38} → {len(res)} results, 0 gateway leaks")
    r = mgmt.get(f"/v1/memories/{lesson_id}")
    assert r.status_code == 404, "existence leaked!"
    print(f"  direct fetch of gateway lesson → {r.status_code} "
          "(404, not 403 — existence doesn't leak)")

    # ── ACT 4 · THE LOOP ────────────────────────────────────────────
    act(4, "THE LOOP — demand surfaces, the team consents, the org learns")
    digest = reviewer.get("/v1/suggestions").json()
    assert digest, "expected promotion candidates from the demand signals"
    for c in digest:
        print(f"  alice's digest: memory {c['memory_id']} ({c['kind']}) — "
              f"{c['blocked_count']} blocked matches · demand teams NAMED "
              f"(content never shown): {c['demand_teams']}")
    candidates = {c["memory_id"]: c for c in digest}
    assert lesson_id in candidates, "the lesson should be a candidate"
    top = candidates[lesson_id]
    assert "sase-management" in top["demand_teams"]

    r = reviewer.post(f"/v1/memories/{top['memory_id']}/promote", json={
        "generalized_content": "Lesson (org-wide): vendor X silently "
                               "rate-limits at 100 rps — batch outbound "
                               "calls; failures are silent."})
    r.raise_for_status()
    promoted = r.json()
    print(f"  promoted → global memory {promoted['id']} "
          f"(derived_from={promoted['derived_from']}, "
          f"promoted_by={promoted['promoted_by']})")

    res = mgmt.post("/v1/memories/search",
                    json={"query": "vendor rate limit batching"}).json()
    show("mgmt agent repeats its search AFTER promotion", res)
    assert any(m["id"] == promoted["id"] and m["scope"] == "global"
               for m in res), "promotion did not reach the demand team"

    # ── ACT 5 · CONTROL ─────────────────────────────────────────────
    act(5, "CONTROL — only the owning team governs its knowledge")
    r = mgmt.post(f"/v1/memories/{promoted['id']}/demote")
    assert r.status_code == 404, "non-owner was able to demote!"
    print(f"  carol tries to demote the global lesson → {r.status_code}")
    r = mgmt.delete(f"/v1/memories/{promoted['id']}")
    assert r.status_code in (404, 409), "non-owner delete slipped through!"
    print(f"  carol tries to delete it → {r.status_code}")

    r = reviewer.post(f"/v1/memories/{promoted['id']}/demote")
    assert r.status_code == 204
    res = mgmt.post("/v1/memories/search",
                    json={"query": "vendor rate limit batching"}).json()
    assert not any(m["id"] == promoted["id"] for m in res), \
        "demotion did not close the wall"
    print("  alice demotes (the review path) → the wall closes again "
          "for other teams")

    print(f"\n{'=' * 62}\n  ALL ASSERTIONS PASSED — write, recall, walls, "
          f"promotion, control.\n{'=' * 62}")


if __name__ == "__main__":
    main()
