"""SQLite storage layer — v1 model.

Identity: a principal is a (user, agent) pair. Users belong to one or more
teams (simulating IdP/SSO team membership); agents belong to a user and
carry a relevance profile. Permissions always derive from the user's
teams; the agent claim exists for provenance and per-agent revocation.

Scoping: two tiers. `team` (default — the floor) and `global` (explicit
opt-in). The visibility predicate is applied inside every read query;
there is no code path that returns unscoped data.

All queries are parameterized. Full-text retrieval uses SQLite FTS5 with
BM25 ranking, trigger-synced from the base table.
"""

import os
import sqlite3
from contextlib import contextmanager

DEFAULT_DB_PATH = os.environ.get("MEMORY_DB_PATH", "memory.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS users (
    id   TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_teams (
    user_id TEXT NOT NULL REFERENCES users(id),
    team_id INTEGER NOT NULL REFERENCES teams(id),
    PRIMARY KEY (user_id, team_id)
);

CREATE TABLE IF NOT EXISTS agents (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL REFERENCES users(id),
    name          TEXT NOT NULL,
    profile_tags  TEXT NOT NULL DEFAULT '',
    profile_kinds TEXT NOT NULL DEFAULT '',
    token_hash    TEXT NOT NULL UNIQUE,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS memories (
    id           INTEGER PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(id),
    agent_id     TEXT NOT NULL REFERENCES agents(id),
    team_id      INTEGER NOT NULL REFERENCES teams(id),
    scope        TEXT NOT NULL DEFAULT 'team' CHECK (scope IN ('team','global')),
    kind         TEXT NOT NULL DEFAULT 'note',
    content      TEXT NOT NULL,
    tags         TEXT NOT NULL DEFAULT '',
    derived_from INTEGER REFERENCES memories(id),
    promoted_by  TEXT REFERENCES users(id),
    promoted_at  TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(scope, team_id);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content, kind, tags,
    content='memories',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content, kind, tags)
    VALUES (new.id, new.content, new.kind, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, kind, tags)
    VALUES ('delete', old.id, old.content, old.kind, old.tags);
END;

-- Demand signal for the promotion loop: a search that MATCHED a team
-- memory but was filtered out by the visibility predicate. Recorded
-- server-side only; surfaced solely to the owning team via suggestions.
CREATE TABLE IF NOT EXISTS blocked_hits (
    id               INTEGER PRIMARY KEY,
    memory_id        INTEGER NOT NULL REFERENCES memories(id),
    requesting_user  TEXT NOT NULL REFERENCES users(id),
    requesting_agent TEXT NOT NULL REFERENCES agents(id),
    query            TEXT NOT NULL,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
"""


def connect(db_path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or DEFAULT_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str | None = None) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def get_conn(db_path: str | None = None):
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# The single visibility predicate. Team membership resolves server-side
# from the principal's user — never from anything the client sends.
VISIBILITY_SQL = (
    "(m.scope = 'global'"
    " OR (m.scope = 'team' AND m.team_id IN"
    "      (SELECT team_id FROM user_teams WHERE user_id = :user_id)))"
)

MEMORY_COLS = (
    "m.id, m.user_id, m.agent_id, m.team_id, m.scope, m.kind, m.content,"
    " m.tags, m.derived_from, m.promoted_by, m.promoted_at, m.created_at,"
    " t.name AS team_name"
)


def fts_query(user_query: str) -> str:
    """Build a safe FTS5 MATCH expression: every token is quoted so FTS5
    operator syntax (AND/OR/NEAR/*/^) in user input stays literal."""
    tokens = [t.replace('"', '""') for t in user_query.split()]
    return " OR ".join(f'"{t}"' for t in tokens)


def user_team_ids(conn, user_id: str) -> list[int]:
    rows = conn.execute(
        "SELECT team_id FROM user_teams WHERE user_id = ?", (user_id,))
    return [r["team_id"] for r in rows]


def team_id_by_name(conn, name: str):
    row = conn.execute("SELECT id FROM teams WHERE name = ?", (name,)).fetchone()
    return row["id"] if row else None


def insert_memory(conn, *, user_id, agent_id, team_id, scope, kind, content,
                  tags, derived_from=None, promoted_by=None) -> int:
    cur = conn.execute(
        """INSERT INTO memories
           (user_id, agent_id, team_id, scope, kind, content, tags,
            derived_from, promoted_by, promoted_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,
                   CASE WHEN ? IS NULL THEN NULL
                        ELSE strftime('%Y-%m-%dT%H:%M:%fZ','now') END)""",
        (user_id, agent_id, team_id, scope, kind, content, tags,
         derived_from, promoted_by, promoted_by),
    )
    return cur.lastrowid


def search_memories(conn, *, user_id, agent_id, query, kind=None, limit=10,
                    record_blocked=True):
    """BM25-ranked search over memories visible to the principal.

    Also records the demand signal: matches that exist at team scope in
    OTHER teams are logged to blocked_hits (never returned, never
    revealed) — the raw material for promotion suggestions.
    """
    match = fts_query(query)
    if not match:
        return recent_memories(conn, user_id=user_id, kind=kind, limit=limit)

    sql = f"""
        SELECT {MEMORY_COLS}, bm25(memories_fts) AS rank
        FROM memories_fts f
        JOIN memories m ON m.id = f.rowid
        JOIN teams t ON t.id = m.team_id
        WHERE memories_fts MATCH :match AND {VISIBILITY_SQL}
    """
    params = {"match": match, "user_id": user_id}
    if kind:
        sql += " AND m.kind = :kind"
        params["kind"] = kind
    sql += " ORDER BY rank LIMIT :limit"
    params["limit"] = limit
    results = [dict(r) for r in conn.execute(sql, params)]

    if record_blocked:
        # Fully parameterized; :match and :user_id are bound values.
        blocked_sql = """
            SELECT m.id FROM memories_fts f
            JOIN memories m ON m.id = f.rowid
            WHERE memories_fts MATCH :match
              AND m.scope = 'team'
              AND m.team_id NOT IN
                  (SELECT team_id FROM user_teams WHERE user_id = :user_id)
            LIMIT 20
        """
        blocked = conn.execute(
            blocked_sql, {"match": match, "user_id": user_id}).fetchall()
        conn.executemany(
            "INSERT INTO blocked_hits (memory_id, requesting_user,"
            " requesting_agent, query) VALUES (?, ?, ?, ?)",
            [(r["id"], user_id, agent_id, query) for r in blocked],
        )
    return results


def recent_memories(conn, *, user_id, kind=None, kinds=None, limit=10):
    sql = f"""
        SELECT {MEMORY_COLS}, NULL AS rank
        FROM memories m JOIN teams t ON t.id = m.team_id
        WHERE {VISIBILITY_SQL}
    """
    params = {"user_id": user_id}
    if kind:
        sql += " AND m.kind = :kind"
        params["kind"] = kind
    if kinds:
        placeholders = ",".join(f":k{i}" for i in range(len(kinds)))
        sql += f" AND m.kind IN ({placeholders})"
        params.update({f"k{i}": k for i, k in enumerate(kinds)})
    sql += " ORDER BY m.created_at DESC, m.id DESC LIMIT :limit"
    params["limit"] = limit
    return [dict(r) for r in conn.execute(sql, params)]


def bootstrap_memories(conn, *, user_id, agent, limit=10):
    """Profile-shaped session bootstrap (doc 07 §7.5): if the agent has a
    relevance profile, search on its topics filtered to its kinds; fill
    any remaining slots with recent visible memories."""
    kinds = [k.strip() for k in (agent.get("profile_kinds") or "").split(",")
             if k.strip()]
    results: list[dict] = []
    if agent.get("profile_tags"):
        results = search_memories(
            conn, user_id=user_id, agent_id=agent["agent_id"],
            query=agent["profile_tags"], limit=limit, record_blocked=False)
        if kinds:
            results = [m for m in results if m["kind"] in kinds]
    if len(results) < limit:
        seen = {m["id"] for m in results}
        fill = recent_memories(conn, user_id=user_id,
                               kinds=kinds or None, limit=limit)
        results += [m for m in fill if m["id"] not in seen]
    return results[:limit]


def get_memory(conn, *, memory_id, user_id):
    sql = f"""
        SELECT {MEMORY_COLS} FROM memories m
        JOIN teams t ON t.id = m.team_id
        WHERE m.id = :memory_id AND {VISIBILITY_SQL}
    """
    row = conn.execute(sql, {"memory_id": memory_id,
                             "user_id": user_id}).fetchone()
    return dict(row) if row else None


def delete_memory(conn, *, memory_id, user_id) -> bool:
    """Curation is a team right: any member of the owning team may retire
    a TEAM memory. Global memories are demoted, never deleted directly."""
    cur = conn.execute(
        """DELETE FROM memories
           WHERE id = ? AND scope = 'team'
             AND team_id IN (SELECT team_id FROM user_teams WHERE user_id = ?)""",
        (memory_id, user_id),
    )
    return cur.rowcount > 0


# Governance: promotion approval is a scoped reviewer role — membership
# in the AI Acceleration team. It grants exactly one extra capability
# (approving demand-backed promotion candidates from the console), never
# broader read or admin rights.
APPROVER_TEAM = "ai-acceleration"

# A candidate leaves the review queue once a global row derived from it
# exists — approving is idempotent per original.
NOT_YET_PROMOTED_SQL = (
    "NOT EXISTS (SELECT 1 FROM memories g"
    " WHERE g.derived_from = m.id AND g.scope = 'global')"
)


def is_promotion_approver(conn, user_id: str) -> bool:
    row = conn.execute(
        """SELECT 1 FROM user_teams ut JOIN teams t ON t.id = ut.team_id
           WHERE ut.user_id = ? AND t.name = ?""",
        (user_id, APPROVER_TEAM),
    ).fetchone()
    return row is not None


def approve_promotion(conn, *, memory_id, approver_id,
                      generalized_content=None):
    """The console governance path: an APPROVER_TEAM member approves a
    demand-backed candidate. Same rewrite semantics as promote_memory —
    a NEW global row derived from the team original — with the approver
    recorded in promoted_by. Raises PermissionError for non-approvers;
    returns None when the memory is not an eligible candidate (unknown,
    not team-scoped, no recorded demand, or already promoted)."""
    if not is_promotion_approver(conn, approver_id):
        raise PermissionError(
            f"promotion approval requires membership in '{APPROVER_TEAM}'")
    # SQL assembled from module constants only; all values are bound.
    candidate_sql = f"""
        SELECT m.* FROM memories m
        WHERE m.id = :memory_id AND m.scope = 'team'
          AND EXISTS (SELECT 1 FROM blocked_hits b WHERE b.memory_id = m.id)
          AND {NOT_YET_PROMOTED_SQL}
    """
    src = conn.execute(candidate_sql, {"memory_id": memory_id}).fetchone()
    if src is None:
        return None
    return insert_memory(
        conn,
        user_id=src["user_id"],          # original author provenance
        agent_id=src["agent_id"],
        team_id=src["team_id"],          # owning team retained for demote rights
        scope="global",
        kind=src["kind"],
        content=generalized_content or src["content"],
        tags=src["tags"],
        derived_from=src["id"],
        promoted_by=approver_id,
    )


def promote_memory(conn, *, memory_id, user_id, generalized_content=None):
    """Promotion is a rewrite, not a copy: a NEW global row derived from
    the team original, carrying promoted-by accountability. Only a member
    of the owning team may promote. Returns the new memory id, or None."""
    src = conn.execute(
        """SELECT m.* FROM memories m
           WHERE m.id = ? AND m.scope = 'team'
             AND m.team_id IN (SELECT team_id FROM user_teams WHERE user_id = ?)""",
        (memory_id, user_id),
    ).fetchone()
    if src is None:
        return None
    return insert_memory(
        conn,
        user_id=src["user_id"],          # original author provenance
        agent_id=src["agent_id"],
        team_id=src["team_id"],          # owning team retained for demote rights
        scope="global",
        kind=src["kind"],
        content=generalized_content or src["content"],
        tags=src["tags"],
        derived_from=src["id"],
        promoted_by=user_id,
    )


def demote_memory(conn, *, memory_id, user_id) -> bool:
    """The review path: a global memory returns to its owning team's scope.
    Only a member of the owning team may demote."""
    cur = conn.execute(
        """UPDATE memories SET scope = 'team'
           WHERE id = ? AND scope = 'global'
             AND team_id IN (SELECT team_id FROM user_teams WHERE user_id = ?)""",
        (memory_id, user_id),
    )
    return cur.rowcount > 0


def suggestions_for_user(conn, *, user_id, per_team_limit=3):
    """The promotion digest (doc 07 §7.3), materialized on demand.

    In production this runs as a weekly batch inside the service boundary;
    the prototype computes it live from blocked_hits. Demand teams are
    NAMED; their queries and content are never disclosed.
    """
    # Fully parameterized; :user_id and :lim are bound values.
    candidates_sql = f"""
        SELECT m.id AS memory_id, m.content, m.kind, m.tags,
               t.name AS team_name, m.team_id,
               COUNT(b.id) AS blocked_count
        FROM memories m
        JOIN blocked_hits b ON b.memory_id = m.id
        JOIN teams t ON t.id = m.team_id
        WHERE m.scope = 'team'
          AND m.team_id IN (SELECT team_id FROM user_teams WHERE user_id = :user_id)
          AND {NOT_YET_PROMOTED_SQL}
        GROUP BY m.id
        ORDER BY blocked_count DESC, m.id
        LIMIT :lim
    """
    candidates = conn.execute(
        candidates_sql, {"user_id": user_id, "lim": per_team_limit}).fetchall()
    return _attach_demand_teams(conn, candidates)


def promotion_queue(conn, *, limit=20):
    """The approver's review queue (console, APPROVER_TEAM only): every
    demand-backed, not-yet-promoted team candidate, org-wide. Approvers
    see only what demand surfaced — never a general read over team
    memories; the visibility predicate still governs everything else."""
    # SQL assembled from module constants only; :lim is a bound value.
    queue_sql = f"""
        SELECT m.id AS memory_id, m.content, m.kind, m.tags,
               t.name AS team_name, m.team_id,
               COUNT(b.id) AS blocked_count
        FROM memories m
        JOIN blocked_hits b ON b.memory_id = m.id
        JOIN teams t ON t.id = m.team_id
        WHERE m.scope = 'team'
          AND {NOT_YET_PROMOTED_SQL}
        GROUP BY m.id
        ORDER BY blocked_count DESC, m.id
        LIMIT :lim
    """
    candidates = conn.execute(queue_sql, {"lim": limit}).fetchall()
    return _attach_demand_teams(conn, candidates)


def _attach_demand_teams(conn, candidates):
    out = []
    for c in candidates:
        demand = conn.execute(
            """SELECT DISTINCT t.name FROM blocked_hits b
               JOIN user_teams ut ON ut.user_id = b.requesting_user
               JOIN teams t ON t.id = ut.team_id
               WHERE b.memory_id = ? AND t.id != ?
               ORDER BY t.name""",
            (c["memory_id"], c["team_id"]),
        ).fetchall()
        out.append({
            "memory_id": c["memory_id"],
            "content": c["content"],
            "kind": c["kind"],
            "team_name": c["team_name"],
            "blocked_count": c["blocked_count"],
            "demand_teams": [d["name"] for d in demand],
        })
    return out
