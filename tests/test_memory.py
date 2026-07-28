"""Write/retrieve behavior beyond scoping — v1 model."""

from conftest import auth
from test_scoping import search, write


def test_write_and_fetch_roundtrip(app_client):
    client, tokens = app_client
    mem_id = write(client, tokens, "a1", "decision: sqlite for prototype",
                   kind="decision")
    r = client.get(f"/v1/memories/{mem_id}", headers=auth(tokens, "a1"))
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "decision"
    assert body["user_id"] == "u1"       # user provenance
    assert body["agent_id"] == "a1"      # agent provenance
    assert body["team_name"] == "team-a"
    assert body["scope"] == "team"       # team is the default


def test_search_ranks_relevant_content(app_client):
    client, tokens = app_client
    write(client, tokens, "a1", "retry policy uses exponential backoff")
    write(client, tokens, "a1", "lunch menu is unrelated content")
    results = search(client, tokens, "a2", "retry backoff policy")
    assert results
    assert "backoff" in results[0]["content"]


def test_empty_query_returns_recent(app_client):
    client, tokens = app_client
    for i in range(3):
        write(client, tokens, "a1", f"note number {i}")
    r = client.get("/v1/memories/recent", headers=auth(tokens, "a1"))
    assert r.status_code == 200
    assert len(r.json()) == 3


def test_bootstrap_is_profile_shaped(app_client):
    client, tokens = app_client
    write(client, tokens, "a1", "decision: retries use backoff with jitter",
          kind="decision")
    write(client, tokens, "a1", "note about the office coffee machine",
          kind="note")
    # a2's profile: tags "backoff retries", kinds decision,lesson
    r = client.get("/v1/memories/bootstrap", headers=auth(tokens, "a2"))
    assert r.status_code == 200
    results = r.json()
    assert results and results[0]["kind"] == "decision"
    assert "backoff" in results[0]["content"]


def test_fts_operators_treated_as_literals(app_client):
    client, tokens = app_client
    write(client, tokens, "a1", "content with AND OR NEAR words")
    r = client.post("/v1/memories/search", headers=auth(tokens, "a1"),
                    json={"query": '"unbalanced AND (NEAR* '})
    assert r.status_code == 200


def test_kind_filter(app_client):
    client, tokens = app_client
    write(client, tokens, "a1", "decision about caching layers",
          kind="decision")
    write(client, tokens, "a1", "note about caching layers", kind="note")
    r = client.post("/v1/memories/search", headers=auth(tokens, "a1"),
                    json={"query": "caching", "kind": "decision"})
    assert r.status_code == 200
    assert {m["kind"] for m in r.json()} == {"decision"}


def test_validation_rejects_bad_scope(app_client):
    client, tokens = app_client
    r = client.post("/v1/memories", headers=auth(tokens, "a1"),
                    json={"content": "x", "scope": "agent"})
    assert r.status_code == 422  # v0 tier no longer exists
    r = client.post("/v1/memories", headers=auth(tokens, "a1"),
                    json={"content": "x", "scope": "everyone"})
    assert r.status_code == 422
