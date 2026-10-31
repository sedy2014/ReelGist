"""errors.py — Exception types shared across the backend."""


class ConfigError(RuntimeError):
    """Server-side misconfiguration (e.g. missing GROQ_API_KEY). Not the caller's fault."""
