#!/usr/bin/env python3

import asyncio
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client

async def list_tools():
    server_url = "http://localhost:9000/sse"

    # 建立 SSE transport
    async with sse_client(
        url=server_url,
        headers={"Content-Type": "application/json"},
    ) as (read_stream, write_stream):
        # 建立 ClientSession
        async with ClientSession(read_stream, write_stream) as session:
            print("Initializing MCP session...")
            init_response = await session.initialize()
            print("Initialize response:", init_response)

            print("Listing tools...")
            response = await session.list_tools()
            # response.tools 是 tools/list 返回的工具列表
            tools = response.tools
            if not tools:
                print("⚠️ No tools found")
            else:
                print("Available MCP tools:")
                print("=" * 60)
                for tool in tools:
                    print(f"🔧 {tool.name}")
                    if hasattr(tool, "description"):
                        print(f"   Description: {tool.description}")
                    input_schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None)
                    if input_schema:
                        props = input_schema.get("properties", {})
                        required = input_schema.get("required", [])
                        if props:
                            print("   Parameters:")
                            for p, info in props.items():
                                ptype = info.get("type", "unknown")
                                pd = info.get("description", "")
                                req = "required" if p in required else "optional"
                                print(f"     • {p} ({ptype}) [{req}]: {pd}")
                    print()
                print("=" * 60)

if __name__ == "__main__":
    asyncio.run(list_tools())
