"""User management for HTTPS VPN server.

Users are stored in /etc/vpn/users.json:
{
  "alice": {"password_hash": "$2b$12$...", "enabled": true},
  "bob":   {"password_hash": "$2b$12$...", "enabled": false}
}

Passwords are hashed with bcrypt via hashlib (no external deps).
"""

import hashlib
import json
import os
import secrets
import sys

USERS_FILE = os.environ.get("VPN_USERS_FILE", "/etc/vpn/users.json")


def _hash_password(password: str) -> str:
    """Hash password with SHA-256 + salt. Simple, no external deps."""
    salt = secrets.token_hex(16)
    h = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}:{h}"


def _verify_password(password: str, stored: str) -> bool:
    """Verify password against stored salt:hash."""
    try:
        salt, h = stored.split(":")
        expected = hashlib.sha256((salt + password).encode()).hexdigest()
        return secrets.compare_digest(h, expected)
    except (ValueError, AttributeError):
        return False


def _load_users() -> dict:
    try:
        with open(USERS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_users(users: dict):
    os.makedirs(os.path.dirname(USERS_FILE), exist_ok=True)
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=2)
    os.chmod(USERS_FILE, 0o600)


def add_user(username: str, password: str) -> bool:
    """Add a new user. Returns False if user already exists."""
    users = _load_users()
    if username in users:
        return False
    users[username] = {
        "password_hash": _hash_password(password),
        "enabled": True,
    }
    _save_users(users)
    return True


def remove_user(username: str) -> bool:
    """Remove a user. Returns False if user doesn't exist."""
    users = _load_users()
    if username not in users:
        return False
    del users[username]
    _save_users(users)
    return True


def enable_user(username: str, enabled: bool = True) -> bool:
    """Enable or disable a user. Returns False if user doesn't exist."""
    users = _load_users()
    if username not in users:
        return False
    users[username]["enabled"] = enabled
    _save_users(users)
    return True


def change_password(username: str, new_password: str) -> bool:
    """Change user's password. Returns False if user doesn't exist."""
    users = _load_users()
    if username not in users:
        return False
    users[username]["password_hash"] = _hash_password(new_password)
    _save_users(users)
    return True


def list_users() -> list[dict]:
    """Return list of users with their status."""
    users = _load_users()
    return [
        {"username": u, "enabled": d.get("enabled", True)}
        for u, d in users.items()
    ]


def authenticate(username: str, password: str) -> bool:
    """Authenticate a user. Returns True if valid and enabled."""
    users = _load_users()
    user = users.get(username)
    if not user:
        return False
    if not user.get("enabled", True):
        return False
    return _verify_password(password, user["password_hash"])


# ── CLI interface ──────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: vpn-users <command> [args]")
        print()
        print("Commands:")
        print("  add <username> <password>    Add a new user")
        print("  remove <username>            Remove a user")
        print("  disable <username>           Disable a user")
        print("  enable <username>            Enable a user")
        print("  passwd <username> <password> Change password")
        print("  list                         List all users")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "add":
        if len(sys.argv) != 4:
            print("Usage: vpn-users add <username> <password>")
            sys.exit(1)
        if add_user(sys.argv[2], sys.argv[3]):
            print(f"User '{sys.argv[2]}' added")
        else:
            print(f"User '{sys.argv[2]}' already exists", file=sys.stderr)
            sys.exit(1)

    elif cmd == "remove":
        if len(sys.argv) != 3:
            print("Usage: vpn-users remove <username>")
            sys.exit(1)
        if remove_user(sys.argv[2]):
            print(f"User '{sys.argv[2]}' removed")
        else:
            print(f"User '{sys.argv[2]}' not found", file=sys.stderr)
            sys.exit(1)

    elif cmd == "disable":
        if len(sys.argv) != 3:
            print("Usage: vpn-users disable <username>")
            sys.exit(1)
        if enable_user(sys.argv[2], False):
            print(f"User '{sys.argv[2]}' disabled")
        else:
            print(f"User '{sys.argv[2]}' not found", file=sys.stderr)
            sys.exit(1)

    elif cmd == "enable":
        if len(sys.argv) != 3:
            print("Usage: vpn-users enable <username>")
            sys.exit(1)
        if enable_user(sys.argv[2], True):
            print(f"User '{sys.argv[2]}' enabled")
        else:
            print(f"User '{sys.argv[2]}' not found", file=sys.stderr)
            sys.exit(1)

    elif cmd == "passwd":
        if len(sys.argv) != 4:
            print("Usage: vpn-users passwd <username> <password>")
            sys.exit(1)
        if change_password(sys.argv[2], sys.argv[3]):
            print(f"Password changed for '{sys.argv[2]}'")
        else:
            print(f"User '{sys.argv[2]}' not found", file=sys.stderr)
            sys.exit(1)

    elif cmd == "list":
        users = list_users()
        if not users:
            print("No users configured")
        else:
            for u in users:
                status = "enabled" if u["enabled"] else "DISABLED"
                print(f"  {u['username']:<20s} [{status}]")

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
