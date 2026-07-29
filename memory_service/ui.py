"""Read-only demo console — GET /ui plus the JSON it reads.

Design constraints (mirroring the production plan):

- SAME enforcement point: every memory read below goes through the exact
  visibility predicate the data API uses (db.VISIBILITY_SQL, via the
  shared db helpers), evaluated as an explicitly selected directory user
  (the console's "view as" selector). The console adds NO unscoped read
  path — switching viewers is how the demo makes the scoping walls
  visible.
- Identity seam, not identity bypass: the console is credential-less for
  the LOCAL demo only. `resolve_viewer` is the single place a real login
  (SSO/OIDC with users and roles) plugs in later without touching any
  query. Until then two demo guards stand in: requests must originate
  from loopback, and the viewer must be an existing directory user.
- Read-only, with ONE governed exception: promotion approval. A viewer
  who is a member of db.APPROVER_TEAM (the AI Acceleration reviewer
  role) sees the org-wide queue of demand-backed candidates and may
  approve a promotion — a scoped capability, not admin. All other
  curation (write, demote, delete) stays with the authenticated agent
  API and the owning team.

The org directory (teams, users, agents) is intentionally shown
unscoped — it simulates IdP data that is org-visible; memory CONTENT is
what the predicate protects.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from . import db
from .models import PromoteRequest

router = APIRouter(prefix="/ui", tags=["demo console"])

STATIC = Path(__file__).resolve().parent / "static"
CONSOLE_HTML = STATIC / "console.html"
RUNBOOK_HTML = STATIC / "runbook.html"

# request.client comes from the ASGI transport (the accepted socket),
# not from any header, so it cannot be spoofed by a remote client.
# "testclient" is Starlette's TestClient placeholder host.
_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def require_local_demo(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in _LOCAL_HOSTS:
        raise HTTPException(
            status_code=403,
            detail="The demo console is loopback-only. Non-local access "
                   "requires the SSO login planned for the production UI.")


def _user_teams(conn, user_id: str) -> list[str]:
    rows = conn.execute(
        """SELECT t.name FROM user_teams ut
           JOIN teams t ON t.id = ut.team_id
           WHERE ut.user_id = ? ORDER BY t.name""", (user_id,))
    return [r["name"] for r in rows]


def resolve_viewer(
    viewer: str = Query(min_length=1, max_length=64,
                        description="Directory user id to view as"),
    _: None = Depends(require_local_demo),
) -> dict:
    """The future authentication seam: today the viewer is a validated
    directory user chosen in the UI; later this dependency exchanges a
    session (SSO/OIDC) for the same {id, name, teams} shape."""
    with db.get_conn() as conn:
        row = conn.execute("SELECT id, name FROM users WHERE id = ?",
                           (viewer,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Unknown viewer")
        return {"id": row["id"], "name": row["name"],
                "teams": _user_teams(conn, row["id"]),
                "can_approve": db.is_promotion_approver(conn, row["id"])}


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def console(_: None = Depends(require_local_demo)) -> HTMLResponse:
    return HTMLResponse(CONSOLE_HTML.read_text(encoding="utf-8"))


@router.get("/runbook", response_class=HTMLResponse, include_in_schema=False)
def runbook(_: None = Depends(require_local_demo)) -> HTMLResponse:
    """The presenter's act-by-act demo script, with copy-paste prompts."""
    return HTMLResponse(RUNBOOK_HTML.read_text(encoding="utf-8"))


@router.get("/api/directory")
def directory(_: None = Depends(require_local_demo)) -> dict:
    """The seeded org directory — feeds the "view as" selector."""
    with db.get_conn() as conn:
        users = [{"id": r["id"], "name": r["name"],
                  "teams": _user_teams(conn, r["id"])}
                 for r in conn.execute(
                     "SELECT id, name FROM users ORDER BY id")]
        teams = [r["name"] for r in conn.execute(
            "SELECT name FROM teams ORDER BY name")]
    return {"users": users, "teams": teams}


@router.get("/api/state")
def state(v: dict = Depends(resolve_viewer)) -> dict:
    """Everything the console shows, as one consistent snapshot,
    evaluated under the selected viewer's visibility."""
    with db.get_conn() as conn:
        agents = [{
            "id": r["id"], "name": r["name"],
            "user_id": r["user_id"], "user_name": r["user_name"],
            "teams": _user_teams(conn, r["user_id"]),
            "profile_tags": r["profile_tags"],
            "profile_kinds": r["profile_kinds"],
            "created_at": r["created_at"],
        } for r in conn.execute(
            """SELECT a.id, a.name, a.user_id, u.name AS user_name,
                      a.profile_tags, a.profile_kinds, a.created_at
               FROM agents a JOIN users u ON u.id = a.user_id
               ORDER BY a.created_at DESC, a.id""")]

        memories = db.recent_memories(conn, user_id=v["id"], limit=200)
        # Approvers review the org-wide demand queue; everyone else sees
        # only their own teams' digest. Memory listings above stay under
        # the normal visibility predicate for both.
        if v["can_approve"]:
            suggestions = db.promotion_queue(conn, limit=20)
        else:
            suggestions = db.suggestions_for_user(conn, user_id=v["id"],
                                                  per_team_limit=10)
    return {"viewer": v, "agents": agents, "memories": memories,
            "suggestions": suggestions}


@router.post("/api/memories/{memory_id}/promote", status_code=201)
def approve_promotion(memory_id: int, body: PromoteRequest,
                      v: dict = Depends(resolve_viewer)) -> dict:
    """Approve a demand-backed promotion candidate — the console's single
    write path, restricted to the AI Acceleration reviewer role. The
    loopback guard applies via resolve_viewer; production swaps that
    seam for SSO and this role check for an IdP group claim."""
    if not v["can_approve"]:
        raise HTTPException(
            status_code=403,
            detail=f"Promotion approval is limited to members of "
                   f"'{db.APPROVER_TEAM}'")
    with db.get_conn() as conn:
        try:
            new_id = db.approve_promotion(
                conn, memory_id=memory_id, approver_id=v["id"],
                generalized_content=body.generalized_content)
        except PermissionError as exc:      # defense in depth at the db layer
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        if new_id is None:
            # Same non-disclosure stance as the data API: ineligible and
            # nonexistent are indistinguishable.
            raise HTTPException(status_code=404,
                                detail="Not an eligible promotion candidate")
        row = db.get_memory(conn, memory_id=new_id, user_id=v["id"])
    return row
