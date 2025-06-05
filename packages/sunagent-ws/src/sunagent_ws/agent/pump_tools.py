import asyncio
import os
from typing import Callable, List

from autogen_ext.tools.mcp import SseServerParams, mcp_server_tools


async def create_tools():
    url = os.environ.get("PUMP_MCP_URL", "")
    if not url:
        return []
        # raise ValueError("PUMP_MCP_URL is not set")
    server_params = SseServerParams(
        url=url,
        headers={},
        timeout=30,  # Connection timeout in seconds
    )
    return await mcp_server_tools(server_params)
