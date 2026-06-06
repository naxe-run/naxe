import json

from mcp.types import TextContent


def _ok(**kwargs) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(kwargs, default=str))]


def _err(msg: str) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps({"error": msg}))]
