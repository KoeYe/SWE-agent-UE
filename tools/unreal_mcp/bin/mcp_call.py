#!/usr/bin/env python3

import os
import sys
import json
import asyncio
from typing import Dict, Any

from fastmcp import Client

def parse_cli_args(argv: list[str]) -> (str, Dict[str, Any]):
    if len(argv) < 2:
        print("Usage: mcp_call_sse <tool_name> [args_json]")
        sys.exit(1)
    tool_name = argv[1]
    args: Dict[str, Any] = {}
    if len(argv) >= 3:
        raw = argv[2]
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                args = parsed
            else:
                args = {"value": parsed}
        except json.JSONDecodeError as e:
            print(f"⚠️ Error parsing JSON args: {e}. Using empty params.")
            args = {}
    return tool_name, args

def clean_and_print_message(msg: str):
    """
    去除每行开头的空白字符，然后打印每行。
    """
    for line in msg.splitlines():
        print(line.lstrip())

async def call_tool_sse(server_url: str, tool_name: str, params: Dict[str, Any]) -> Any:
    async with Client(server_url) as client:
        try:
            # 可选 ping
            try:
                pong = await client.ping()
                # 如果你不想显示 ping 成功，也可以注释下面这行
                # print(f"Ping response: {pong}")
            except Exception as e_ping:
                print(f"⚠️ Ping failed (continuing): {e_ping}")

            print(f"Calling tool '{tool_name}' with params: {params}")
            result = await client.call_tool(tool_name, params)

            # 尝试提取字典 JSON
            extracted: Dict[str, Any] | None = None

            # 优先查 structured data 属性
            if hasattr(result, "data") and isinstance(result.data, dict):
                extracted = result.data
            elif hasattr(result, "structured_content") and isinstance(result.structured_content, dict):
                extracted = result.structured_content
            elif hasattr(result, "structuredContent") and isinstance(result.structuredContent, dict):
                extracted = result.structuredContent

            # 如果上面没拿到 dict，再看 content 里的 text JSON
            if extracted is None:
                content = getattr(result, "content", None)
                if content:
                    for block in content:
                        text = getattr(block, "text", None)
                        if isinstance(text, str):
                            txt = text.strip()
                            if txt.startswith("{") or txt.startswith("["):
                                try:
                                    extracted = json.loads(txt)
                                    break
                                except Exception:
                                    # 解析失败就跳过
                                    pass

            # 如果还是没拿到结构化 JSON，就打印原始内容以便调试
            if not isinstance(extracted, dict):
                print("❗ Could not extract structured JSON from result. Raw content:")
                content = getattr(result, "content", None)
                if content:
                    for block in content:
                        t = getattr(block, "text", None)
                        if isinstance(t, str):
                            print(t.lstrip())
                else:
                    # 完全没有 content 的话就打印 result 对象本身
                    print(result)
                return result

            # 有结构化 dict，检查 success
            success_val = extracted.get("success")
            if success_val is True or str(success_val).lower() == "true":
                message = extracted.get("message", "")
                clean_and_print_message(message)
            else:
                print("❌ Tool returned success = false or missing field.")
                print(json.dumps(extracted, indent=2, ensure_ascii=False))

            return result

        except Exception as e:
            print("❌ Error calling tool:", str(e))
            return {"error": str(e)}

def main():
    tool_name, params = parse_cli_args(sys.argv)
    server_url = os.getenv("UNREAL_MCP_URL", "http://localhost:9000/sse")
    asyncio.run(call_tool_sse(server_url, tool_name, params))

if __name__ == "__main__":
    main()
