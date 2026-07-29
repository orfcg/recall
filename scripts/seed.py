"""Seed the org directory: teams, users, and team memberships.

This simulates what production reads from the IdP/SSO directory — it is
deliberately separate from agent registration (scripts/add_agent.py),
because in the real system AI Acceleration never creates identities:
users already exist, and agents are added self-service.

Usage:  python scripts/seed.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory_service import db

TEAMS = ["sase-gateway", "sase-management", "sase-platform",
         "ai-acceleration"]

# (user_id, display name, [teams]) — note bob spans multiple teams AND
# holds the ai-acceleration membership, which is the scoped reviewer
# role that may approve promotions from the console (db.APPROVER_TEAM).
USERS = [
    ("alice", "Alice", ["sase-gateway"]),
    ("bob", "Bob", ["sase-gateway", "sase-platform", "ai-acceleration"]),
    ("carol", "Carol", ["sase-management"]),
]


def main() -> None:
    db.init_db()
    with db.get_conn() as conn:
        for team in TEAMS:
            conn.execute("INSERT OR IGNORE INTO teams (name) VALUES (?)",
                         (team,))
        team_ids = {r["name"]: r["id"]
                    for r in conn.execute("SELECT id, name FROM teams")}
        for user_id, name, teams in USERS:
            conn.execute("INSERT OR IGNORE INTO users (id, name) VALUES (?, ?)",
                         (user_id, name))
            for team in teams:
                conn.execute(
                    "INSERT OR IGNORE INTO user_teams (user_id, team_id)"
                    " VALUES (?, ?)",
                    (user_id, team_ids[team]))
    print("directory seeded:", ", ".join(TEAMS))
    for user_id, name, teams in USERS:
        print(f"  {user_id:<6} → {', '.join(teams)}")
    print("\nnext: register agents with scripts/add_agent.py "
          "(simulates the self-service add-agent skill)")


if __name__ == "__main__":
    main()
