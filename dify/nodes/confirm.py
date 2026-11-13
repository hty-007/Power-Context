import json


def main(saved: str, answer: str, new_code: str, previous_code: str, op: str) -> dict:
    try:
        result = json.loads(saved)
    except (ValueError, TypeError):
        result = {}
    status = result.get("status") if isinstance(result, dict) else None
    if status == "saved":
        return {"answer": answer, "active_code": new_code}
    if op == "new":
        message = "这次未收到开档确认。请发送 **开始** 再试一次。"
    elif status == "conflict":
        message = f"这份存档已有更新。请发送 `继续 {new_code}` 查看当前进度。"
    else:
        message = f"这次未收到写入确认。请发送 `继续 {new_code}` 核对进度，再决定下一步。"
    return {"answer": message, "active_code": previous_code}
