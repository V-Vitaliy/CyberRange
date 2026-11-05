import hashlib


def hash_ctf_flag(flag_value: str) -> str:
    """Hashes a CTF flag value using SHA-256 for secure database storage and comparison."""
    return hashlib.sha256(flag_value.encode("utf-8")).hexdigest()
