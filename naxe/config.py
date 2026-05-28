import os
from pathlib import Path

_CONFIG_FILE = Path.home() / ".config" / "naxe" / "config"


def resolve_db_url() -> str:
    if url := os.environ.get("NAXE_DB_URL"):
        return url
    if _CONFIG_FILE.exists():
        url = _CONFIG_FILE.read_text().strip()
        if url:
            return url
    if path := os.environ.get("NAXE_DB_PATH"):
        return path
    return "./naxe.db"


def resolve_db_url_with_source() -> tuple[str, str]:
    if url := os.environ.get("NAXE_DB_URL"):
        return url, "env:NAXE_DB_URL"
    if _CONFIG_FILE.exists():
        url = _CONFIG_FILE.read_text().strip()
        if url:
            return url, f"config:{_CONFIG_FILE}"
    if path := os.environ.get("NAXE_DB_PATH"):
        return path, "env:NAXE_DB_PATH"
    return "./naxe.db", "default"


def write_config_url(url: str) -> None:
    _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_FILE.write_text(url + "\n")
