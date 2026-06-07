import os
from pathlib import Path

_CONFIG_FILE = Path.home() / ".config" / "naxe" / "config"
_THEME_FILE = Path.home() / ".config" / "naxe" / "theme"
_CONTEXT_FILE = Path.home() / ".config" / "naxe" / "context"

DEFAULT_THEME = "naxe"


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


def resolve_theme() -> str:
    if theme := os.environ.get("NAXE_THEME"):
        return theme.strip()
    if _THEME_FILE.exists():
        theme = _THEME_FILE.read_text().strip()
        if theme:
            return theme
    return DEFAULT_THEME


def resolve_theme_with_source() -> tuple[str, str]:
    if theme := os.environ.get("NAXE_THEME"):
        return theme.strip(), "env:NAXE_THEME"
    if _THEME_FILE.exists():
        theme = _THEME_FILE.read_text().strip()
        if theme:
            return theme, f"config:{_THEME_FILE}"
    return DEFAULT_THEME, "default"


def write_theme(theme: str) -> None:
    _THEME_FILE.parent.mkdir(parents=True, exist_ok=True)
    _THEME_FILE.write_text(theme + "\n")


def resolve_context() -> str | None:
    if ctx := os.environ.get("NAXE_CONTEXT"):
        return ctx.strip() or None
    if _CONTEXT_FILE.exists():
        ctx = _CONTEXT_FILE.read_text().strip()
        if ctx:
            return ctx
    return None


def resolve_context_with_source() -> tuple[str | None, str]:
    if ctx := os.environ.get("NAXE_CONTEXT"):
        return ctx.strip() or None, "env:NAXE_CONTEXT"
    if _CONTEXT_FILE.exists():
        ctx = _CONTEXT_FILE.read_text().strip()
        if ctx:
            return ctx, f"config:{_CONTEXT_FILE}"
    return None, "default"


def write_context(context: str) -> None:
    _CONTEXT_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CONTEXT_FILE.write_text(context + "\n")
