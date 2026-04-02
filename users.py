#!/usr/bin/env python3
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
import tempfile

USERS_FILE = os.environ.get("VPN_USERS_FILE", "/etc/vpn/users.json")

# Dummy salt for timing-safe auth when user doesn't exist
_DUMMY_SALT = secrets.token_bytes(16)

# In-memory user cache with mtime tracking
_users_cache: dict | None = None
_users_mtime: float = 0.0


def _hash_password(password: str) -> str:
    """Hash password with scrypt (key-stretching, GPU-resistant)."""
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt:{salt.hex()}:{h.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    """Verify password against stored hash. Supports scrypt and legacy SHA-256."""
    try:
        if stored.startswith("scrypt:"):
            _, salt_hex, h_hex = stored.split(":")
            salt = bytes.fromhex(salt_hex)
            expected = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
            return secrets.compare_digest(expected.hex(), h_hex)
        else:
            # Legacy SHA-256 format (salt:hash) — still verify, but new passwords use scrypt
            salt, h = stored.split(":")
            expected = hashlib.sha256((salt + password).encode()).hexdigest()
            return secrets.compare_digest(h, expected)
    except (ValueError, AttributeError):
        return False


def _dummy_verify(password: str):
    """Run scrypt with dummy salt so timing is identical to a real check."""
    hashlib.scrypt(password.encode(), salt=_DUMMY_SALT, n=2**14, r=8, p=1, dklen=32)


def _load_users() -> dict:
    """Load users from disk with mtime-based caching."""
    global _users_cache, _users_mtime
    try:
        st = os.stat(USERS_FILE)
        if _users_cache is not None and st.st_mtime == _users_mtime:
            return _users_cache
        with open(USERS_FILE) as f:
            _users_cache = json.load(f)
            _users_mtime = st.st_mtime
            return _users_cache
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        _users_cache = {}
        _users_mtime = 0.0
        return _users_cache


def _save_users(users: dict):
    """Atomic save: write to temp file then rename."""
    global _users_cache, _users_mtime
    dir_path = os.path.dirname(USERS_FILE)
    os.makedirs(dir_path, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(users, f, indent=2)
        os.chmod(tmp_path, 0o600)
        os.rename(tmp_path, USERS_FILE)
        # Update cache
        _users_cache = users
        _users_mtime = os.stat(USERS_FILE).st_mtime
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


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
    """Authenticate a user. Returns True if valid and enabled.

    Runs scrypt even for non-existent users to prevent timing oracle.
    Auto-rehashes legacy SHA-256 passwords to scrypt on successful login.
    """
    users = _load_users()
    user = users.get(username)
    if not user:
        _dummy_verify(password)
        return False
    if not user.get("enabled", True):
        _dummy_verify(password)
        return False
    stored = user["password_hash"]
    if not _verify_password(password, stored):
        return False
    # Auto-rehash legacy SHA-256 to scrypt
    if not stored.startswith("scrypt:"):
        user["password_hash"] = _hash_password(password)
        _save_users(users)
    return True


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

    elif cmd == "add-stdin":
        # Password from VPN_STDIN_PASS env var (avoids ps/procfs leak)
        if len(sys.argv) != 3:
            print("Usage: VPN_STDIN_PASS=<password> vpn-users add-stdin <username>")
            sys.exit(1)
        password = os.environ.get("VPN_STDIN_PASS", "")
        if not password:
            print("VPN_STDIN_PASS environment variable is empty", file=sys.stderr)
            sys.exit(1)
        if add_user(sys.argv[2], password):
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
