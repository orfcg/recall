"""API request/response models — v1."""

from typing import Literal, Optional

from pydantic import BaseModel, Field

# Two tiers: team is the floor and the default; global is an explicit,
# deliberate opt-in (and in practice mostly earned via promotion).
Scope = Literal["team", "global"]

# Small opinionated vocabulary: distilled knowledge, not transcripts.
# `lesson` is the natural org-wide (global) candidate.
Kind = Literal["decision", "convention", "insight", "artifact", "note",
               "lesson", "postmortem"]


class MemoryWrite(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    scope: Scope = "team"
    kind: Kind = "note"
    tags: str = Field(default="", max_length=500)
    # Which of the caller's teams owns this memory. Optional when the
    # user belongs to exactly one team; required otherwise.
    team: Optional[str] = None


class MemoryOut(BaseModel):
    id: int
    user_id: str
    agent_id: str
    team_name: str
    scope: Scope
    kind: str
    content: str
    tags: str
    created_at: str
    derived_from: Optional[int] = None
    promoted_by: Optional[str] = None
    promoted_at: Optional[str] = None
    rank: Optional[float] = None


class SearchRequest(BaseModel):
    query: str = Field(default="", max_length=1000)
    kind: Optional[Kind] = None
    limit: int = Field(default=10, ge=1, le=50)


class PromoteRequest(BaseModel):
    # The generalized rewrite the team approves (doc 07 §7.3). Falls back
    # to the original content if omitted.
    generalized_content: Optional[str] = Field(default=None, max_length=8000)


class SuggestionOut(BaseModel):
    memory_id: int
    content: str
    kind: str
    team_name: str
    blocked_count: int
    demand_teams: list[str]


class WhoAmI(BaseModel):
    user_id: str
    user_name: str
    agent_id: str
    agent_name: str
    teams: list[str]
