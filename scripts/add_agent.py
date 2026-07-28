"""Simulates the self-service `add-agent` skill (doc 07 §7.7).

In production the skill runs on the developer's machine: it authenticates
the developer via an SSO device flow, calls the service's registration
endpoint (which derives team membership from IdP claims), and holds the
resulting short-lived token in the session environment or OS keychain.

The prototype simulates that flow locally: it registers the agent under
an existing user from the seeded directory, mints a random token, stores
only its SHA-256 hash in the database, and writes the plaintext to
.tokens.json — a gitignored, chmod-600 DEMO device standing in for the
keychain. Nothing is ever hardcoded or committed.

Usage:
  python scripts/add_agent.py --user alice --agent gw-code-reviewer \
      --name "Gateway Code Review Agent" \
      --tags "conventions reviews backoff" --kinds decision,convention
"""

import argparse
import json
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory_service import db
from memory_service.auth import hash_token

TOKENS_FILE = Path(__file__).resolve().parents[1] / ".tokens.json"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--user", required=True, help="existing user id (from seed)")
    ap.add_argument("--agent", required=True, help="new agent id")
    ap.add_argument("--name", default=None, help="agent display name")
    ap.add_argument("--tags", default="", help="relevance profile: topic tags")
    ap.add_argument("--kinds", default="",
                    help="relevance profile: comma-separated kinds")
    args = ap.parse_args()

    db.init_db()
    with db.get_conn() as conn:
        user = conn.execute("SELECT id FROM users WHERE id = ?",
                            (args.user,)).fetchone()
        if user is None:
            sys.exit(f"unknown user '{args.user}' — run scripts/seed.py first")
        if conn.execute("SELECT 1 FROM agents WHERE id = ?",
                        (args.agent,)).fetchone():
            sys.exit(f"agent '{args.agent}' already registered")
        token = secrets.token_urlsafe(32)
        conn.execute(
            """INSERT INTO agents
               (id, user_id, name, profile_tags, profile_kinds, token_hash)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (args.agent, args.user, args.name or args.agent,
             args.tags, args.kinds, hash_token(token)))

    existing = json.loads(TOKENS_FILE.read_text()) if TOKENS_FILE.exists() else {}
    existing[args.agent] = token
    TOKENS_FILE.write_text(json.dumps(existing, indent=2) + "\n")
    TOKENS_FILE.chmod(0o600)
    # Log only identifiers and destination — never credential values.
    destination = TOKENS_FILE.name
    print(f"agent '{args.agent}' registered for user '{args.user}'; "
          f"local file written: {destination} (gitignored, mode 600)")


if __name__ == "__main__":
    main()
