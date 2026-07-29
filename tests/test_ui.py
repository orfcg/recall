"""The demo console must enforce the SAME visibility predicate as the
agent API — switching viewers is how the demo shows the walls, so a
console that leaked would break the core guarantee. Its single write
path, promotion approval, is restricted to the ai-acceleration reviewer
role and to demand-backed candidates."""

from conftest import auth
from test_scoping import search, write


def test_console_page_serves(app_client):
    client, _ = app_client
    r = client.get("/ui")
    assert r.status_code == 200
    assert "Recall" in r.text
    assert "text/html" in r.headers["content-type"]


def test_runbook_page_serves(app_client):
    client, _ = app_client
    r = client.get("/ui/runbook")
    assert r.status_code == 200
    assert "runbook" in r.text


def test_directory_lists_users_with_teams(app_client):
    client, _ = app_client
    r = client.get("/ui/api/directory")
    assert r.status_code == 200
    users = {u["id"]: u["teams"] for u in r.json()["users"]}
    assert users == {"u1": ["team-a"], "u2": ["team-a", "team-b"],
                     "u3": ["team-b"], "u4": ["ai-acceleration"]}


def test_state_rejects_unknown_viewer(app_client):
    client, _ = app_client
    assert client.get("/ui/api/state?viewer=mallory").status_code == 404
    assert client.get("/ui/api/state").status_code == 422  # viewer required


def test_state_applies_visibility_predicate(app_client):
    client, tokens = app_client
    write(client, tokens, "a1", "team-a retry policy", scope="team")
    write(client, tokens, "a1", "org-wide vendor lesson", scope="global")

    same_team = [m["content"] for m in
                 client.get("/ui/api/state?viewer=u1").json()["memories"]]
    assert "team-a retry policy" in same_team
    assert "org-wide vendor lesson" in same_team

    other_team = [m["content"] for m in
                  client.get("/ui/api/state?viewer=u3").json()["memories"]]
    assert "team-a retry policy" not in other_team  # the wall holds
    assert "org-wide vendor lesson" in other_team   # global crosses it


def test_state_suggestions_only_for_owning_team(app_client):
    client, tokens = app_client
    mem_id = write(client, tokens, "a1", "exponential backoff cadence")
    assert search(client, tokens, "b1", "exponential backoff") == []  # demand

    owner = client.get("/ui/api/state?viewer=u1").json()["suggestions"]
    assert [s["memory_id"] for s in owner] == [mem_id]
    assert owner[0]["demand_teams"] == ["team-b"]

    outsider = client.get("/ui/api/state?viewer=u3").json()["suggestions"]
    assert outsider == []


def test_agents_listed_regardless_of_viewer(app_client):
    client, _ = app_client
    ids = {a["id"] for a in
           client.get("/ui/api/state?viewer=u3").json()["agents"]}
    assert ids == {"a1", "a2", "b1"}


def _promote(client, viewer, memory_id, rewrite=None):
    return client.post(
        f"/ui/api/memories/{memory_id}/promote?viewer={viewer}",
        json={"generalized_content": rewrite})


def test_state_flags_the_approver_role(app_client):
    client, _ = app_client
    assert client.get("/ui/api/state?viewer=u1").json()["viewer"]["can_approve"] is False
    assert client.get("/ui/api/state?viewer=u4").json()["viewer"]["can_approve"] is True


def test_approver_queue_is_org_wide_but_memories_stay_scoped(app_client):
    client, tokens = app_client
    mem_id = write(client, tokens, "a1", "exponential backoff cadence")
    assert search(client, tokens, "b1", "exponential backoff") == []  # demand

    state = client.get("/ui/api/state?viewer=u4").json()
    # u4 is in no product team, yet reviews the demand-backed candidate…
    assert [s["memory_id"] for s in state["suggestions"]] == [mem_id]
    # …while the ordinary memory listing still hides team-a's content.
    assert all(m["id"] != mem_id for m in state["memories"])


def test_non_approver_cannot_promote_from_console(app_client):
    client, tokens = app_client
    mem_id = write(client, tokens, "a1", "team-a retry policy")
    assert search(client, tokens, "b1", "retry policy") == []  # demand
    # u1 owns the memory but holds no approver role — the console path
    # is the role's, not the owner's.
    assert _promote(client, "u1", mem_id).status_code == 403


def test_approver_promotes_demand_backed_candidate(app_client):
    client, tokens = app_client
    mem_id = write(client, tokens, "a1", "vendor X silent timeout lesson")
    assert search(client, tokens, "b1", "vendor timeout") == []  # demand

    r = _promote(client, "u4", mem_id, rewrite="Org-wide: vendor X calls "
                 "drop silently under load; treat as retryable.")
    assert r.status_code == 201
    promoted = r.json()
    assert promoted["scope"] == "global"
    assert promoted["promoted_by"] == "u4"
    assert promoted["derived_from"] == mem_id
    assert promoted["content"].startswith("Org-wide:")

    # The global rewrite crossed the wall; the team original did not.
    other_team = [m["content"] for m in
                  client.get("/ui/api/state?viewer=u3").json()["memories"]]
    assert any(c.startswith("Org-wide:") for c in other_team)
    assert "vendor X silent timeout lesson" not in other_team

    # Approved candidates leave the review queue (and the owner digest).
    assert client.get("/ui/api/state?viewer=u4").json()["suggestions"] == []
    assert client.get("/ui/api/state?viewer=u1").json()["suggestions"] == []


def test_console_promotion_requires_recorded_demand(app_client):
    client, tokens = app_client
    mem_id = write(client, tokens, "a1", "no one asked for this")
    assert _promote(client, "u4", mem_id).status_code == 404   # no demand
    assert _promote(client, "u4", 99999).status_code == 404    # unknown
