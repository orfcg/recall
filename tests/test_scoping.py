"""Scoping is the core guarantee — v1 model: team floor, global opt-in,
multi-team users, team curation."""

from conftest import auth


def write(client, tokens, agent, content, scope="team", kind="note",
          team=None, tags=""):
    body = {"content": content, "scope": scope, "kind": kind, "tags": tags}
    if team:
        body["team"] = team
    r = client.post("/v1/memories", headers=auth(tokens, agent), json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def search(client, tokens, agent, query):
    r = client.post("/v1/memories/search", headers=auth(tokens, agent),
                    json={"query": query})
    assert r.status_code == 200, r.text
    return r.json()


def test_same_team_sees_team_memory(app_client):
    client, tokens = app_client
    write(client, tokens, "a1", "gateway uses exponential backoff")
    # a2 belongs to u2 who is also in team-a
    results = search(client, tokens, "a2", "exponential backoff")
    assert any("backoff" in m["content"] for m in results)


def test_other_team_cannot_see_team_memory(app_client):
    client, tokens = app_client
    mem_id = write(client, tokens, "a1", "gateway uses exponential backoff")
    assert search(client, tokens, "b1", "exponential backoff") == []
    r = client.get(f"/v1/memories/{mem_id}", headers=auth(tokens, "b1"))
    assert r.status_code == 404  # existence doesn't leak


def test_multi_team_user_sees_both_teams(app_client):
    client, tokens = app_client
    write(client, tokens, "a1", "team-a fact about widgets")
    write(client, tokens, "b1", "team-b fact about widgets")
    results = search(client, tokens, "a2", "widgets")
    teams = {m["team_name"] for m in results}
    assert teams == {"team-a", "team-b"}


def test_multi_team_user_must_specify_team_on_write(app_client):
    client, tokens = app_client
    r = client.post("/v1/memories", headers=auth(tokens, "a2"),
                    json={"content": "ambiguous ownership"})
    assert r.status_code == 400
    write(client, tokens, "a2", "explicit ownership", team="team-b")


def test_cannot_write_to_foreign_team(app_client):
    client, tokens = app_client
    r = client.post("/v1/memories", headers=auth(tokens, "a1"),
                    json={"content": "x", "team": "team-b"})
    assert r.status_code == 400


def test_global_memory_visible_to_all(app_client):
    client, tokens = app_client
    write(client, tokens, "a1", "org convention: shared auth middleware",
          scope="global", kind="convention")
    for agent in ["a1", "a2", "b1"]:
        assert search(client, tokens, agent, "auth middleware"), agent


def test_any_team_member_can_retire_team_memory(app_client):
    client, tokens = app_client
    mem_id = write(client, tokens, "a1", "team fact to retire")
    # u2 (agent a2) is a member of team-a but NOT the author
    r = client.delete(f"/v1/memories/{mem_id}", headers=auth(tokens, "a2"))
    assert r.status_code == 204
    assert search(client, tokens, "a1", "team fact") == []


def test_non_member_cannot_retire_team_memory(app_client):
    client, tokens = app_client
    mem_id = write(client, tokens, "a1", "team fact stays")
    r = client.delete(f"/v1/memories/{mem_id}", headers=auth(tokens, "b1"))
    assert r.status_code == 404


def test_global_memory_cannot_be_deleted_directly(app_client):
    client, tokens = app_client
    mem_id = write(client, tokens, "a1", "global fact", scope="global")
    r = client.delete(f"/v1/memories/{mem_id}", headers=auth(tokens, "a1"))
    assert r.status_code == 409  # demote via review instead


def test_unauthenticated_rejected(app_client):
    client, _ = app_client
    assert client.post("/v1/memories/search",
                       json={"query": "x"}).status_code == 401
    r = client.post("/v1/memories/search", json={"query": "x"},
                    headers={"Authorization": "Bearer not-a-real-value"})
    assert r.status_code == 401
