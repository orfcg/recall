"""FastAPI app exposing the scoped memory API — v1 model.

Endpoints (all data endpoints require a bearer token = user+agent principal):
  POST   /v1/memories               write (scope: team default | global opt-in)
  POST   /v1/memories/search        BM25 search over visible memories;
                                    records cross-team demand signals
  GET    /v1/memories/recent        recent visible memories
  GET    /v1/memories/bootstrap     profile-shaped session bootstrap
  GET    /v1/memories/{id}          fetch one (404 if invisible — no leaks)
  DELETE /v1/memories/{id}          retire — any member of the owning team
  POST   /v1/memories/{id}/promote  team → global (owning team only)
  POST   /v1/memories/{id}/demote   global → team (owning team only)
  GET    /v1/suggestions            the promotion digest for caller's teams
  GET    /v1/whoami                 principal identity
  GET    /healthz                   liveness

Plus the read-only, loopback-only demo console (see ui.py):
  GET    /ui                        the console page
  GET    /ui/api/directory          org directory (feeds "view as")
  GET    /ui/api/state              viewer-scoped snapshot
"""

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException

from . import db
from .auth import get_current_principal
from .models import (MemoryOut, MemoryWrite, PromoteRequest, SearchRequest,
                     SuggestionOut, WhoAmI)
from .ui import router as ui_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Recall — Scoped Agent Memory Service", version="1.0.0",
              lifespan=lifespan)
app.include_router(ui_router)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/v1/whoami", response_model=WhoAmI)
def whoami(p: dict = Depends(get_current_principal)):
    return WhoAmI(user_id=p["user_id"], user_name=p["user_name"],
                  agent_id=p["agent_id"], agent_name=p["agent_name"],
                  teams=p["team_names"])


def _resolve_team(p: dict, requested: str | None) -> int:
    """Which of the caller's teams owns the write. The requested name must
    be one of the USER's teams — ownership cannot be pointed elsewhere."""
    if requested is None:
        if len(p["team_ids"]) == 1:
            return p["team_ids"][0]
        raise HTTPException(
            status_code=400,
            detail=f"You belong to multiple teams; specify one of: "
                   f"{', '.join(p['team_names'])}")
    for tid, tname in zip(p["team_ids"], p["team_names"]):
        if tname == requested:
            return tid
    raise HTTPException(status_code=400,
                        detail="Not a member of the requested team")


@app.post("/v1/memories", response_model=MemoryOut, status_code=201)
def write_memory(body: MemoryWrite, p: dict = Depends(get_current_principal)):
    team_id = _resolve_team(p, body.team)
    with db.get_conn() as conn:
        memory_id = db.insert_memory(
            conn, user_id=p["user_id"], agent_id=p["agent_id"],
            team_id=team_id, scope=body.scope, kind=body.kind,
            content=body.content, tags=body.tags)
        row = db.get_memory(conn, memory_id=memory_id, user_id=p["user_id"])
    return MemoryOut(**row)


@app.post("/v1/memories/search", response_model=list[MemoryOut])
def search(body: SearchRequest, p: dict = Depends(get_current_principal)):
    with db.get_conn() as conn:
        rows = db.search_memories(
            conn, user_id=p["user_id"], agent_id=p["agent_id"],
            query=body.query, kind=body.kind, limit=body.limit)
    return [MemoryOut(**r) for r in rows]


@app.get("/v1/memories/recent", response_model=list[MemoryOut])
def recent(limit: int = 10, p: dict = Depends(get_current_principal)):
    with db.get_conn() as conn:
        rows = db.recent_memories(conn, user_id=p["user_id"],
                                  limit=min(max(limit, 1), 50))
    return [MemoryOut(**r) for r in rows]


@app.get("/v1/memories/bootstrap", response_model=list[MemoryOut])
def bootstrap(limit: int = 10, p: dict = Depends(get_current_principal)):
    with db.get_conn() as conn:
        rows = db.bootstrap_memories(conn, user_id=p["user_id"], agent=p,
                                     limit=min(max(limit, 1), 50))
    return [MemoryOut(**r) for r in rows]


@app.get("/v1/memories/{memory_id}", response_model=MemoryOut)
def get_one(memory_id: int, p: dict = Depends(get_current_principal)):
    with db.get_conn() as conn:
        row = db.get_memory(conn, memory_id=memory_id, user_id=p["user_id"])
    if row is None:
        # 404 for both "doesn't exist" and "not visible" — existence of
        # another team's memory must never leak through the data plane.
        raise HTTPException(status_code=404, detail="Memory not found")
    return MemoryOut(**row)


@app.delete("/v1/memories/{memory_id}", status_code=204)
def delete_one(memory_id: int, p: dict = Depends(get_current_principal)):
    with db.get_conn() as conn:
        visible = db.get_memory(conn, memory_id=memory_id,
                                user_id=p["user_id"])
        if visible and visible["scope"] == "global":
            raise HTTPException(
                status_code=409,
                detail="Global memories are demoted via review, not deleted")
        deleted = db.delete_memory(conn, memory_id=memory_id,
                                   user_id=p["user_id"])
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found")


@app.post("/v1/memories/{memory_id}/promote", response_model=MemoryOut,
          status_code=201)
def promote(memory_id: int, body: PromoteRequest,
            p: dict = Depends(get_current_principal)):
    with db.get_conn() as conn:
        new_id = db.promote_memory(
            conn, memory_id=memory_id, user_id=p["user_id"],
            generalized_content=body.generalized_content)
        if new_id is None:
            raise HTTPException(status_code=404, detail="Memory not found")
        row = db.get_memory(conn, memory_id=new_id, user_id=p["user_id"])
    return MemoryOut(**row)


@app.post("/v1/memories/{memory_id}/demote", status_code=204)
def demote(memory_id: int, p: dict = Depends(get_current_principal)):
    with db.get_conn() as conn:
        ok = db.demote_memory(conn, memory_id=memory_id, user_id=p["user_id"])
    if not ok:
        raise HTTPException(status_code=404, detail="Memory not found")


@app.get("/v1/suggestions", response_model=list[SuggestionOut])
def suggestions(p: dict = Depends(get_current_principal)):
    """The promotion digest (doc 07 §7.3). Production: a weekly batch;
    prototype: computed on demand from recorded demand signals."""
    with db.get_conn() as conn:
        rows = db.suggestions_for_user(conn, user_id=p["user_id"])
    return [SuggestionOut(**r) for r in rows]
