"""Bearer-token authentication — v1 principal model.

A token represents a (user, agent) pair: permissions derive from the
USER's team memberships (resolved server-side from the directory tables,
simulating IdP/SSO claims); the AGENT claim rides along for provenance
and per-agent revocation. In production the add-agent skill obtains a
short-lived token via an SSO device flow; the prototype simulates this
with local demo tokens minted by scripts/add_agent.py and stored only as
SHA-256 hashes.
"""

import hashlib

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import db

bearer = HTTPBearer(auto_error=False)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def get_current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> dict:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token_hash = hash_token(credentials.credentials)
    with db.get_conn() as conn:
        row = conn.execute(
            """
            SELECT a.id AS agent_id, a.name AS agent_name,
                   a.profile_tags, a.profile_kinds,
                   u.id AS user_id, u.name AS user_name
            FROM agents a JOIN users u ON u.id = a.user_id
            WHERE a.token_hash = ?
            """,
            (token_hash,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        principal = dict(row)
        teams = conn.execute(
            """SELECT t.id, t.name FROM user_teams ut
               JOIN teams t ON t.id = ut.team_id
               WHERE ut.user_id = ? ORDER BY t.name""",
            (principal["user_id"],),
        ).fetchall()
    principal["team_ids"] = [t["id"] for t in teams]
    principal["team_names"] = [t["name"] for t in teams]
    return principal
