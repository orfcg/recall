import secrets
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    """TestClient on a throwaway database.

    Directory: team-a, team-b, ai-acceleration
               u1∈{a}, u2∈{a,b}, u3∈{b}, u4∈{ai-acceleration} (approver)
    Agents:    a1(u1), a2(u2, with relevance profile), b1(u3)
    """
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("MEMORY_DB_PATH", db_path)

    for mod in ["memory_service.main", "memory_service.auth",
                "memory_service.db", "memory_service.models",
                "memory_service.ui", "memory_service"]:
        sys.modules.pop(mod, None)

    from memory_service import db
    from memory_service.auth import hash_token
    from memory_service.main import app
    from fastapi.testclient import TestClient

    db.init_db()
    tokens = {}
    with db.get_conn() as conn:
        conn.execute("INSERT INTO teams (name) VALUES ('team-a'), ('team-b'),"
                     " ('ai-acceleration')")
        team_ids = {r["name"]: r["id"]
                    for r in conn.execute("SELECT id, name FROM teams")}
        users = {"u1": ["team-a"], "u2": ["team-a", "team-b"],
                 "u3": ["team-b"], "u4": ["ai-acceleration"]}
        for uid, teams in users.items():
            conn.execute("INSERT INTO users (id, name) VALUES (?, ?)",
                         (uid, uid))
            for t in teams:
                conn.execute(
                    "INSERT INTO user_teams (user_id, team_id) VALUES (?, ?)",
                    (uid, team_ids[t]))
        agents = [
            ("a1", "u1", "", ""),
            ("a2", "u2", "backoff retries", "decision,lesson"),
            ("b1", "u3", "", ""),
        ]
        for agent_id, uid, tags, kinds in agents:
            token = secrets.token_urlsafe(16)
            conn.execute(
                """INSERT INTO agents
                   (id, user_id, name, profile_tags, profile_kinds, token_hash)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (agent_id, uid, agent_id, tags, kinds, hash_token(token)))
            tokens[agent_id] = token

    client = TestClient(app)
    yield client, tokens


def auth(tokens, agent_id):
    return {"Authorization": f"Bearer {tokens[agent_id]}"}
