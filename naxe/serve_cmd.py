from __future__ import annotations

import argparse
import sys

from naxe.config import resolve_db_url, resolve_context
from naxe.http_app import build_app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="naxe serve")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="Bind port (default: 8080)")
    args = parser.parse_args(argv)

    db_url = resolve_db_url()
    context = resolve_context()
    app = build_app(db_url, context)

    print(f"naxe serve  http://{args.host}:{args.port}")
    print(f"  SSE MCP   http://{args.host}:{args.port}/sse")
    print(f"  REST API  http://{args.host}:{args.port}/api/v1/")

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main(sys.argv[1:])
