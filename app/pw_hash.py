"""
Password hashing — Argon2id (preferred) with transparent bcrypt upgrade.

New passwords are hashed with Argon2id (RFC 9106 / OWASP-recommended
memory-hard algorithm, resistant to GPU/ASIC brute force).

Existing bcrypt hashes are still verified correctly. On successful login the
caller should invoke `needs_rehash()` and, when True, re-hash with `hash()`.
This completes the migration to Argon2id transparently at login time without
requiring a forced password reset.

Bcrypt hashes are identified by the "$2b$" or "$2a$" prefix.
"""

import argon2  # argon2-cffi

_ph = argon2.PasswordHasher(
    time_cost=2,  # iterations (OWASP 2024 minimum: 2)
    memory_cost=65536,  # 64 MiB
    parallelism=2,
    hash_len=32,
    salt_len=16,
    type=argon2.Type.ID,
)


def hash(password: str) -> str:
    """Return an Argon2id hash of *password*."""
    return _ph.hash(password)


def verify(password: str, stored_hash: str) -> bool:
    """
    Return True if *password* matches *stored_hash*.

    Supports both Argon2id (new) and bcrypt (legacy) hashes.
    Raises no exception on mismatch — returns False instead.
    """
    if _is_bcrypt(stored_hash):
        import bcrypt as _bcrypt  # pyright: ignore[reportMissingImports]

        try:
            return _bcrypt.checkpw(password.encode(), stored_hash.encode())
        except Exception:
            return False
    try:
        return _ph.verify(stored_hash, password)
    except argon2.exceptions.VerifyMismatchError:
        return False
    except Exception:
        return False


def needs_rehash(stored_hash: str) -> bool:
    """
    Return True if *stored_hash* should be upgraded to a fresh Argon2id hash.

    Always True for bcrypt hashes. Also True if the Argon2id parameters have
    changed (argon2-cffi checks this automatically via check_needs_rehash).
    """
    if _is_bcrypt(stored_hash):
        return True
    return _ph.check_needs_rehash(stored_hash)


def _is_bcrypt(h: str) -> bool:
    return h.startswith(("$2b$", "$2a$", "$2y$"))


# Pre-computed Argon2id dummy hash used to equalise timing when no user record
# is found (prevents timing-based account enumeration — CWE-208).
DUMMY_HASH: str = hash("dummy-timing-equalization-placeholder")
