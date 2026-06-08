"""naxe — unified CLI entry point."""

import sys


def main() -> None:
    args = sys.argv[1:]

    if not args:
        from naxe.server import main as _server
        _server()
        return

    cmd = args[0]

    if cmd == "init":
        sys.argv = [sys.argv[0]] + args[1:]
        from naxe.init_cmd import main as _init
        _init()

    elif cmd == "ui":
        from naxe.tui import main as _ui
        _ui()

    elif cmd == "config":
        sys.argv = [sys.argv[0]] + args[1:]
        from naxe.cli_config import main as _config
        _config()

    elif cmd == "serve":
        from naxe.serve_cmd import main as _serve
        _serve(args[1:])

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print()
        print("Usage: naxe [command]")
        print()
        print("Commands:")
        print("  (no args)   Start the MCP server")
        print("  init        Run the setup wizard")
        print("  ui          Open the terminal UI")
        print("  config      Manage configuration")
        print("  serve       Start the HTTP/SSE server")
        sys.exit(1)
