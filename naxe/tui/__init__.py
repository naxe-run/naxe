import argparse
import sys

from naxe.config import resolve_db_url


def main() -> None:
    parser = argparse.ArgumentParser(prog="naxe ui")
    parser.add_argument("--host", default=None, help="Remote naxe server URL (e.g. http://localhost:8080)")
    parser.add_argument("--api-key", default=None, help="API key for remote server auth")
    args = parser.parse_args(sys.argv[2:])

    if args.host:
        from naxe.tui.client import RemoteNaxeClient
        client = RemoteNaxeClient(args.host, args.api_key)
    else:
        from naxe.schema import get_connection
        from naxe.tui.client import LocalNaxeClient
        client = LocalNaxeClient(get_connection(resolve_db_url(), readonly=False))

    from naxe.tui.app import NaxeUI
    NaxeUI(client=client).run()
