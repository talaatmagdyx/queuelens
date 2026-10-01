"""QueueLens tests."""

import secrets


def cred() -> str:
    """Throwaway credential generated per run — no secret-looking literals in the repo."""
    return secrets.token_urlsafe(12)
