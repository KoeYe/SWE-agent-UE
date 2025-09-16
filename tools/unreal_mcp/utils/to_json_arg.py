#!/usr/bin/env python3
import sys
import json
from pathlib import Path

def main():
    if len(sys.argv) < 2:
        print("Usage: python to_json_arg.py <script.py>")
        sys.exit(1)

    file_path = Path(sys.argv[1])
    if not file_path.exists():
        print(f"❌ File not found: {file_path}")
        sys.exit(1)

    # 读取 Python 文件内容
    code = file_path.read_text(encoding="utf-8")

    # 包装成 JSON
    json_arg = json.dumps({"script": code})

    print(json_arg)

if __name__ == "__main__":
    main()
