#!/usr/bin/env python3

import os
import sys
import json
import asyncio
import base64
from typing import Dict, Any
from pathlib import Path
import sys, os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))


from fastmcp import Client
from utils.params_parser import robust_parse_third_arg

# -----------------------------
# Config
# -----------------------------
MAX_PRINT_LENGTH = 10000  # If response is longer than this, save to file
DEFAULT_PREFIX = "mcp_response"


# -----------------------------
# Parse CLI arguments
# -----------------------------
def parse_cli_args(argv: list[str]) -> (str, Dict[str, Any]):
    if len(argv) < 2:
        print("Usage: mcp_call <tool_name> '<args_json>'")
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
            print(parsed)
        except json.JSONDecodeError as e:
            print(f"⚠️ Error parsing JSON args: {e}. Trying to fix...")
            try:
                # Attempt to fix escaped characters
                args = robust_parse_third_arg(raw, tool_name)
                print(f"✅ Parsed args after fix: {args}")
            except Exception as e2:
                print(f"❌ Still invalid JSON: {e2}. Using empty params.")
                args = {}
    return tool_name, args

# -----------------------------
# Printing / File saving helpers
# -----------------------------
def dump_to_workspace(content: str, filename_prefix: str = DEFAULT_PREFIX) -> str:
    """
    Save long content into a file under $WORK_SPACE (or current dir).
    Returns the absolute path.
    """
    workspace = os.getenv("WORK_SPACE", os.getcwd())
    Path(workspace).mkdir(parents=True, exist_ok=True)

    file_path = Path(workspace) / f"{filename_prefix}.txt"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    return str(file_path.resolve())


def safe_print(content: str, filename_prefix: str = DEFAULT_PREFIX):
    """
    Print content if short enough, otherwise save to file and print file path.
    """
    if len(content) > MAX_PRINT_LENGTH:
        file_path = dump_to_workspace(content, filename_prefix)
        print(f"📄 Response too long, saved to file: {file_path}")
    else:
        print(content)


def clean_and_print_message(msg: str):
    """
    Print message line by line, stripping leading whitespace.
    """
    for line in msg.splitlines():
        print(line.lstrip())


def format_base64_images(content: str) -> tuple[str, list[str]]:
    """
    Detect base64 image strings in JSON, replace with placeholders,
    and return extracted Markdown image list.
    """
    import re
    patterns = [
        (r'"base64":\s*"([A-Za-z0-9+/]{100,}={0,2})"', "base64"),
        (r'"image":\s*"([A-Za-z0-9+/]{100,}={0,2})"', "image"),
        (r'"screenshot":\s*"([A-Za-z0-9+/]{100,}={0,2})"', "screenshot"),
        (r'"capture":\s*"([A-Za-z0-9+/]{100,}={0,2})"', "capture"),
    ]

    cleaned_content = content
    extracted_images = []

    for pattern, field_name in patterns:
        matches = list(re.finditer(pattern, cleaned_content))
        for match in reversed(matches):
            base64_data = match.group(1)
            if len(base64_data) > 100:
                image_title = f"{field_name.title()} {len(extracted_images) + 1}"
                markdown_image = f"![{image_title}](data:image/png;base64,{base64_data})"
                extracted_images.insert(0, markdown_image)
                placeholder = f'"[IMAGE_{len(extracted_images)}: {image_title}]"'
                cleaned_content = (
                    cleaned_content[: match.start()]
                    + f'"{field_name}": {placeholder}'
                    + cleaned_content[match.end() :]
                )

    extracted_images.reverse()
    return cleaned_content, extracted_images

# -----------------------------
# Call MCP tool
# -----------------------------
async def call_tool_sse(server_url: str, tool_name: str, params: Dict[str, Any]) -> Any:
    async with Client(server_url) as client:
        try:
            # Optional ping check
            try:
                await client.ping()
            except Exception as e_ping:
                print(f"⚠️ Ping failed (continuing): {e_ping}")

            print(f"Calling tool '{tool_name}' with params: {params}")
            result = await client.call_tool(tool_name, params)

            # Try to extract structured dict
            extracted: Dict[str, Any] | None = None
            if hasattr(result, "data") and isinstance(result.data, dict):
                extracted = result.data
            elif hasattr(result, "structured_content") and isinstance(result.structured_content, dict):
                extracted = result.structured_content
            elif hasattr(result, "structuredContent") and isinstance(result.structuredContent, dict):
                extracted = result.structuredContent

            # Try parse JSON text if structured not found
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
                                    pass

            # If still no dict, print raw content
            if not isinstance(extracted, dict):
                print("❗ Could not extract structured JSON from result. Raw content:")
                content = getattr(result, "content", None)
                if content:
                    for block in content:
                        t = getattr(block, "text", None)
                        if isinstance(t, str):
                            safe_print(t.lstrip(), "raw_content")
                else:
                    print(result)
                return result

            # Handle success case
            success_val = extracted.get("success")
            if success_val is True or str(success_val).lower() == "true":
                message = extracted.get("message", "")
                json_str = json.dumps(extracted, indent=2, ensure_ascii=False)
                cleaned_json, extracted_images = format_base64_images(json_str)

                if extracted_images:
                    safe_print("📋 Response (images extracted):", "response")
                    safe_print(cleaned_json, "response_json")
                    print(f"\n🖼️  Found {len(extracted_images)} image(s):")
                    for i, image_markdown in enumerate(extracted_images, 1):
                        safe_print(f"\nImage {i}:\n{image_markdown}", f"image_{i}")
                else:
                    safe_print(message, "response_message")
            else:
                # Handle error case
                # print("❌ Tool returned success = false or missing field.")
                json_str = json.dumps(extracted, indent=2, ensure_ascii=False)
                cleaned_json, error_images = format_base64_images(json_str)
                safe_print(cleaned_json, "response")
                if error_images:
                    print(f"\n🖼️  Images in error response:")
                    for image_markdown in error_images:
                        safe_print(image_markdown, "image")

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
