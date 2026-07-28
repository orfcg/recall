"""The promotion loop: demand signals, suggestions, promote, demote."""

from conftest import auth
from test_scoping import search, write


def make_demand(client, tokens, mem_content="vendor rate limits at 100 rps"):
    """a1 (team-a) writes a lesson; b1 (team-b) searches for it and is
    blocked — generating the demand signal."""
    mem_id = write(client, tokens, "a1", mem_content, kind="lesson",
                   tags="vendor rate-limit")
    for _ in range(3):
        assert search(client, tokens, "b1", "vendor rate limits") == []
    return mem_id


def test_blocked_searches_become_suggestions(app_client):
    client, tokens = app_client
    mem_id = make_demand(client, tokens)
    r = client.get("/v1/suggestions", headers=auth(tokens, "a1"))
    assert r.status_code == 200
    digest = r.json()
    assert digest and digest[0]["memory_id"] == mem_id
    assert digest[0]["blocked_count"] >= 3
    # demand team is NAMED to the owning team
    assert digest[0]["demand_teams"] == ["team-b"]


def test_no_suggestions_without_demand(app_client):
    client, tokens = app_client
    write(client, tokens, "a1", "quiet team fact nobody searched for")
    r = client.get("/v1/suggestions", headers=auth(tokens, "a1"))
    assert r.json() == []


def test_suggestions_only_for_own_teams(app_client):
    client, tokens = app_client
    make_demand(client, tokens)
    # b1's team owns no candidate — the demand it generated is not its own
    r = client.get("/v1/suggestions", headers=auth(tokens, "b1"))
    assert r.json() == []


def test_promote_creates_global_rewrite_with_provenance(app_client):
    client, tokens = app_client
    mem_id = make_demand(client, tokens)
    r = client.post(f"/v1/memories/{mem_id}/promote", headers=auth(tokens, "a1"),
                    json={"generalized_content": "org lesson: batch vendor calls"})
    assert r.status_code == 201, r.text
    promoted = r.json()
    assert promoted["scope"] == "global"
    assert promoted["derived_from"] == mem_id
    assert promoted["promoted_by"] == "u1"
    assert promoted["content"] == "org lesson: batch vendor calls"
    # original stays team-scoped
    orig = client.get(f"/v1/memories/{mem_id}",
                      headers=auth(tokens, "a1")).json()
    assert orig["scope"] == "team"
    # the demand team can now retrieve the promoted version
    results = search(client, tokens, "b1", "batch vendor calls")
    assert any(m["id"] == promoted["id"] for m in results)


def test_only_owning_team_can_promote(app_client):
    client, tokens = app_client
    mem_id = make_demand(client, tokens)
    r = client.post(f"/v1/memories/{mem_id}/promote",
                    headers=auth(tokens, "b1"), json={})
    assert r.status_code == 404


def test_demote_closes_the_wall_and_is_owner_only(app_client):
    client, tokens = app_client
    mem_id = make_demand(client, tokens)
    promoted = client.post(f"/v1/memories/{mem_id}/promote",
                           headers=auth(tokens, "a1"), json={}).json()
    # non-owner cannot demote
    r = client.post(f"/v1/memories/{promoted['id']}/demote",
                    headers=auth(tokens, "b1"))
    assert r.status_code == 404
    # owner demotes; the demand team loses access again
    r = client.post(f"/v1/memories/{promoted['id']}/demote",
                    headers=auth(tokens, "a1"))
    assert r.status_code == 204
    results = search(client, tokens, "b1", "vendor rate limits")
    assert not any(m["id"] == promoted["id"] for m in results)
