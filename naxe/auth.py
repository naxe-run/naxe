import hashlib
import os

KEY_PREFIX = "naxe_sk_"


def generate_key() -> str:
    """Generate a random API key. Returns the raw key — shown once, never stored."""
    return KEY_PREFIX + os.urandom(32).hex()


def hash_key(raw_key: str) -> str:
    """SHA-256 hash of the raw key. This is what gets stored in the DB."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


def validate_key_format(key: str) -> bool:
    """Check key has correct prefix and length (prefix + 64 hex chars)."""
    return key.startswith(KEY_PREFIX) and len(key) == len(KEY_PREFIX) + 64
