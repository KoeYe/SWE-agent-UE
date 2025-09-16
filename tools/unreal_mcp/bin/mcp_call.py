#!/usr/bin/env python3
import os
import sys
import json
import asyncio
from typing import Dict, Any

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from fastmcp import Client
from utils.argparse_parser import create_mcp_parser


# -----------------------------
# Parse CLI arguments with enhanced parser
# -----------------------------
def parse_cli_args(argv: list[str]) -> (str, Dict[str, Any]):
    if len(argv) < 2:
        print("Usage: mcp_call <tool_name> [--param=value] [--param2=value2] ...")
        print("       mcp_call <tool_name> '{\"param\": \"value\"}'  # JSON format also supported")
        sys.exit(1)

    tool_name = argv[1]
    args: Dict[str, Any] = {}

    if len(argv) >= 3:
        # Join all remaining arguments to handle complex values
        args_str = " ".join(argv[2:])
        parser = create_mcp_parser()
        args = parser.parse_args(tool_name, args_str)
    
    return tool_name, args


# -----------------------------
# Helpers to extract results
# -----------------------------
def try_parse_json(text: str):
    try:
        return json.loads(text)
    except Exception:
        return None


def extract_result_dict(result) -> Dict[str, Any] | None:
    """ Try to extract a dict from the result object"""
    for attr in ("data", "structured_content", "structuredContent"):
        val = getattr(result, attr, None)
        if isinstance(val, dict):
            return val

    content = getattr(result, "content", []) or []
    for block in content:
        txt = getattr(block, "text", None)
        if not isinstance(txt, str):
            continue
        txt = txt.strip()
        if not txt or txt[0] not in "{[":
            continue
        parsed = try_parse_json(txt)
        if parsed is not None:
            return parsed
    return None


def print_result(result):
    """ print the result in a friendly way """
    extracted = extract_result_dict(result)
    if extracted is not None:
        print(json.dumps(extracted, indent=2, ensure_ascii=False))
        return

    content = getattr(result, "content", None)
    if content:
        for block in content:
            txt = getattr(block, "text", None)
            if isinstance(txt, str):
                print(txt.strip())
    else:
        print(result)


# -----------------------------
# Call MCP tool
# -----------------------------
async def call_tool_sse(server_url: str, tool_name: str, params: Dict[str, Any]):
    print(f"Calling tool '{tool_name}' with params: {params}")
    
    # Show script preview if it exists
    if 'script' in params and params['script']:
        script_lines = params['script'].split('\n')
        print(f"Script preview ({len(script_lines)} lines):")
        for i, line in enumerate(script_lines[:5], 1):
            print(f"  {i}: {line}")
        if len(script_lines) > 5:
            print(f"  ... and {len(script_lines) - 5} more lines")
    
    async with Client(server_url) as client:
        try:
            result = await client.call_tool(tool_name, params)
            print_result(result)
            return result
        except Exception as e:
            print("❌ Error calling tool:", str(e))
            return {"error": str(e)}


# -----------------------------
# Main entry point
# -----------------------------
def main():
    tool_name, params = parse_cli_args(sys.argv)
    server_url = os.getenv("UNREAL_MCP_URL", "http://localhost:9000/sse")
    asyncio.run(call_tool_sse(server_url, tool_name, params))


if __name__ == "__main__":
    main()
