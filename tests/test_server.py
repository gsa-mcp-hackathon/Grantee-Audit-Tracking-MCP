import asyncio

from fastmcp import Client

from dat_mcp.app import mcp


def test_server_imports():
    assert mcp.name == "DAT Grantee MCP Server"


def test_every_tool_is_registered_with_described_parameters():
    """What an agent sees: each tool's description and parameter descriptions."""

    async def list_tools():
        async with Client(mcp) as client:
            return await client.list_tools()

    tools = asyncio.run(list_tools())
    assert tools
    for tool in tools:
        assert tool.description, tool.name
        for name, schema in tool.input_schema["properties"].items():
            assert schema.get("description"), f"{tool.name}.{name} has no Args: entry"
        # Every tool is listed in the server instructions' tool guide.
        assert tool.name in mcp.instructions, tool.name
