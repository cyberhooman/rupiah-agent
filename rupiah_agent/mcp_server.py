"""The same tools as an MCP server, so Claude Desktop, Claude Code or any MCP client can use them.

Claude Code: claude mcp add rupiah -- python -m rupiah_agent.mcp_server
"""
from mcp.server.fastmcp import FastMCP

from .tools import FUNCS

mcp = FastMCP("rupiah")
for fn in FUNCS.values():
    mcp.tool()(fn)

if __name__ == "__main__":
    mcp.run()
