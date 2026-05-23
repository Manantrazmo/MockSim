"""
bcrypt password hashing — thin wrapper so callers don't need bcrypt
encoding/byte-decoding boilerplate everywhere.
"""
from __future__ import annotations

import bcrypt


def hash_password(plain: str) -> str:
    """Hash a plaintext password. Result is the standard bcrypt $2b$… string."""
    if not plain:
        raise ValueError("password must be non-empty")
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(plain.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time compare. Returns False on any error (malformed hash, etc)."""
    if not plain or not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False
