#!/usr/bin/env python3
import sys, os, json, ast, re, base64, binascii
from typing import Any, Dict, Tuple
from urllib.parse import unquote_plus

SMART_QUOTES = {
    '“': '"', '”': '"', '„': '"', '‟': '"',
    '‘': "'", '’': "'", '‚': "'", '‛': "'",
}
ZERO_WIDTH = ''.join([
    '\u200b', '\u200c', '\u200d', '\u2060', '\ufeff'
])

CODE_FENCE_RE = re.compile(
    r"(?s)^\s*```([a-zA-Z0-9_]*)\s*\n(.*?)\n```"
)

def _normalize(s: str) -> str:
    # 统一换行、去 BOM/零宽字符、替换智能引号
    s = s.replace('\r\n', '\n').replace('\r', '\n')
    for z in ZERO_WIDTH:
        s = s.replace(z, '')
    for k, v in SMART_QUOTES.items():
        s = s.replace(k, v)
    # 去掉明显的前后噪声（比如 “🎬 ACTION …\n” 、“🧰 DEBUG …”）
    # 只保留从第一个 “{”、“[”、“`” 或 非空白字符 开始
    trimmed = s.lstrip()
    # 如果前面有一堆日志，尝试从第一个 JSON/围栏开始截取
    first_json_like = min(
        [i for i in [
            trimmed.find('{'),
            trimmed.find('['),
            trimmed.find('```')
        ] if i >= 0] or [0]
    )
    if first_json_like > 0:
        trimmed = trimmed[first_json_like:]
    return trimmed.strip()

def _strip_outer_quotes(s: str) -> str:
    # 去掉一层整体包裹的引号 '...'/ "..."
    if len(s) >= 2 and ((s[0] == s[-1] == '"') or (s[0] == s[-1] == "'")):
        return s[1:-1]
    return s

def _try_json(text: str):
    try:
        return json.loads(text)
    except Exception:
        return None

def _try_python_literal_dict(text: str):
    # 例如：{'script': 'print("hi")'}（单引号的 Python 字面量）
    try:
        val = ast.literal_eval(text)
        if isinstance(val, dict):
            return val
        return None
    except Exception:
        return None

def _extract_code_fence(s: str):
    """
    匹配 ```lang\n ... \n``` 形式。
    返回 (lang, content) 或 (None, None)
    """
    m = CODE_FENCE_RE.match(s)
    if not m:
        return None, None
    lang = (m.group(1) or '').lower().strip()
    content = m.group(2)
    return lang, content

def _extract_first_balanced_json(text: str):
    """
    从文本里用括号计数提取第一段平衡的 JSON 对象 {...} 或数组 [...]
    忽略字符串中的括号；失败返回 None
    """
    i = 0
    in_str = False
    esc = False
    quote = ''
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if not in_str and ch in '{[':
            if depth == 0:
                start = i
            depth += 1
        elif not in_str and ch in '}]':
            if depth > 0:
                depth -= 1
                if depth == 0 and start != -1:
                    segment = text[start:i+1]
                    obj = _try_json(segment)
                    if obj is not None:
                        return segment
        elif ch in ['"', "'"]:
            if not in_str:
                in_str = True
                quote = ch
                esc = False
            else:
                if not esc and ch == quote:
                    in_str = False
                esc = False
        elif ch == '\\' and in_str:
            esc = not esc
        else:
            esc = False
    return None

def _looks_like_python(code: str) -> bool:
    # 简单启发式判断是不是 Python 代码
    kw_hits = sum(k in code for k in [
        'import ', 'def ', 'class ', 'print(',
        'for ', 'while ', 'try:', 'except', 'with ',
        'unreal.', '\n'
    ])
    return kw_hits >= 1

def _maybe_path_arg(s: str) -> Dict[str, Any] | None:
    ss = s.strip()
    if ss.endswith('.py'):
        # 不强制要求文件存在；存在就更靠谱
        return {"path": ss}
    return None

def _try_base64_json(s: str):
    # 如果是 base64 包了个 JSON
    try:
        raw = base64.b64decode(s, validate=True)
        val = _try_json(raw.decode('utf-8', errors='ignore'))
        return val
    except (binascii.Error, ValueError):
        return None

def _kvs_fallback(s: str) -> Dict[str, Any] | None:
    """
    处理 script=... / path=... / value=... 这种最简 kv 形式
    script= 后面若匹配到围栏或引号块，就提取；否则取整串
    """
    m_path = re.search(r'\bpath\s*=\s*([^\s]+)', s)
    if m_path:
        return {"path": _strip_outer_quotes(m_path.group(1))}
    m_script = re.search(r'\bscript\s*=\s*(.+)$', s, flags=re.S)
    if m_script:
        body = m_script.group(1).strip()
        # 可能是围栏
        lang, content = _extract_code_fence(body)
        if content:
            return {"script": content}
        # 或者是引号包裹
        body = _strip_outer_quotes(body)
        return {"script": body}
    return None

def _decode_unicode_escapes(s: str) -> str:
    # 把 "\\n" 还原成真正换行；仅用于已确认是代码/文本时
    try:
        return s.encode('utf-8').decode('unicode_escape')
    except Exception:
        return s

def robust_parse_third_arg(raw: str, tool_name: str) -> Dict[str, Any]:
    """
    对第三个参数做分层解析：
    1) 直接 JSON
    2) 代码围栏（```json / ```python）
    3) 文本中抽取第一段平衡 JSON
    4) Python 字面量 dict
    5) URL decode / Base64 JSON
    6) kv 退化形式（script=..., path=...）
    7) .py 路径猜测
    8) 若是 execute_python_script 且像代码 → {"script": ...}
    9) 兜底 {"value": 原串}
    """
    if not raw or not raw.strip():
        return {}

    s = _normalize(raw)

    # 1) 直解析 JSON
    val = _try_json(s)
    if val is not None:
        if isinstance(val, dict):
            return val
        return {"value": val}

    # 2) 代码围栏
    lang, content = _extract_code_fence(s)
    if content:
        if lang in ('json', 'javascript', 'jsonc'):
            j = _try_json(content)
            if j is not None:
                return j if isinstance(j, dict) else {"value": j}
        # 其他语言视为脚本
        if tool_name == 'execute_python_script':
            return {"script": content}
        return {"value": content}

    # 3) 抽取第一段平衡 JSON 片段
    seg = _extract_first_balanced_json(s)
    if seg:
        j = _try_json(seg)
        if j is not None:
            return j if isinstance(j, dict) else {"value": j}

    # 4) Python 字面量 dict（单引号）
    lit = _try_python_literal_dict(s)
    if lit is not None:
        return lit

    # 5) URL / Base64 尝试
    maybe_url = unquote_plus(s)
    if maybe_url != s:
        j = _try_json(maybe_url)
        if j is not None:
            return j if isinstance(j, dict) else {"value": j}
    b64 = _try_base64_json(s)
    if b64 is not None:
        return b64 if isinstance(b64, dict) else {"value": b64}

    # 6) 简单 kv 退化形式
    kv = _kvs_fallback(s)
    if kv is not None:
        return kv

    # 7) .py 路径猜测
    path_guess = _maybe_path_arg(s)
    if path_guess is not None:
        return path_guess

    # 8) 若是执行脚本：把它当作“脚本字符串”，并做一次转义还原
    if tool_name == 'execute_python_script':
        # 去掉一层整体引号
        s2 = _strip_outer_quotes(s)
        # 若像 JSON 的 "script":"...." 但坏了，尽量取右边
        m = re.search(r'"script"\s*:\s*"(.*)"\s*$', s2, flags=re.S)
        if m:
            suspect_code = m.group(1)
            return {"script": _decode_unicode_escapes(suspect_code)}
        # 直接视作脚本
        return {"script": _decode_unicode_escapes(s2)}

    # 9) 兜底
    return {"value": s}
