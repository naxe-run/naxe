import sys
from naxe.config import (
    resolve_db_url_with_source, write_config_url, _CONFIG_FILE,
    resolve_theme_with_source, write_theme, _THEME_FILE,
)


def main():
    args = sys.argv[1:]

    if not args:
        print("Usage: naxe-config <command> [args]")
        print()
        print("Commands:")
        print("  set-url <url>      Save a DB URL to ~/.config/naxe/config")
        print("  get-url            Print the currently resolved DB URL and its source")
        print("  set-theme <name>   Save a default theme to ~/.config/naxe/theme")
        print("  get-theme          Print the currently resolved theme and its source")
        sys.exit(1)

    command = args[0]

    if command == "set-url":
        if len(args) < 2:
            print("Usage: naxe-config set-url <url>", file=sys.stderr)
            sys.exit(1)
        write_config_url(args[1])
        print(f"Saved to {_CONFIG_FILE}")

    elif command == "get-url":
        url, source = resolve_db_url_with_source()
        print(f"{url}  ({source})")

    elif command == "set-theme":
        if len(args) < 2:
            print("Usage: naxe-config set-theme <name>", file=sys.stderr)
            print("Built-in naxe themes: naxe, naxe-bold")
            sys.exit(1)
        write_theme(args[1])
        print(f"Saved to {_THEME_FILE}")

    elif command == "get-theme":
        theme, source = resolve_theme_with_source()
        print(f"{theme}  ({source})")

    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        sys.exit(1)
