import sys
from naxe.config import resolve_db_url_with_source, write_config_url, _CONFIG_FILE


def main():
    args = sys.argv[1:]

    if not args:
        print("Usage: naxe-config <command> [args]")
        print()
        print("Commands:")
        print("  set-url <url>   Save a DB URL to ~/.config/naxe/config")
        print("  get-url         Print the currently resolved DB URL and its source")
        sys.exit(1)

    command = args[0]

    if command == "set-url":
        if len(args) < 2:
            print("Usage: naxe-config set-url <url>", file=sys.stderr)
            sys.exit(1)
        url = args[1]
        write_config_url(url)
        print(f"Saved to {_CONFIG_FILE}")

    elif command == "get-url":
        url, source = resolve_db_url_with_source()
        print(f"{url}  ({source})")

    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        sys.exit(1)
