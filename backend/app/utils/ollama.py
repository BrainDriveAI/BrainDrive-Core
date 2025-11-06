from urllib.parse import unquote


def normalize_server_base(url: str) -> str:
    """Normalize Ollama server base URL by trimming common suffixes."""
    url = unquote(url).strip()
    url = url.rstrip("/")
    if url.endswith("/api/pull"):
        url = url[: -len("/api/pull")]
    if url.endswith("/api"):
        url = url[: -len("/api")]
    return url


def make_dedupe_key(server_base: str, name: str) -> str:
    """Create a stable idempotency key for model installs."""
    return f"{server_base}|{name}"
