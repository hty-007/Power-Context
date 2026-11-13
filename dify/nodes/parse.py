import json
import re
import unicodedata


CODE = re.compile(r"H-[0-9A-F]{24}")
ACTIONS = {"修灯", "问潮", "送信", "引航", "放船", "开闸", "鸣铃"}


def main(query: str, active_code: str) -> dict:
    words = unicodedata.normalize("NFKC", query[:240]).strip().split()
    active_code = active_code or ""
    command = words[0].lower() if words else ""
    explicit_code = words[1].upper() if len(words) == 2 else ""
    code = explicit_code or active_code
    action = ""
    op = "help"

    if len(words) == 1 and command in {"开始", "新游戏", "new"}:
        op = "new"
        code = ""
    elif len(words) <= 2 and command in {"继续", "resume"}:
        op = "resume" if CODE.fullmatch(code) else "need_code"
    elif len(words) == 1 and CODE.fullmatch(command.upper()):
        op = "resume"
        code = command.upper()
    elif len(words) == 1 and command in ACTIONS:
        op = "move" if CODE.fullmatch(code) else "need_code"
        action = command
    elif words and command not in {"帮助", "help", "怎么玩"}:
        op = "unknown"

    load = {"op": op}
    if op in {"resume", "move"}:
        load["code"] = code
    return {
        "op": op,
        "action": action,
        "code": code,
        "previous_code": active_code,
        "load_body": json.dumps(load, separators=(",", ":")),
    }
