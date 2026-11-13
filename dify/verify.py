import argparse
import getpass
import json
import os
import re
import secrets
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


SAVE_CODE = re.compile(r"H-[0-9A-F]{24}")
ROOT = Path(__file__).resolve().parent


def request_json(url, payload, headers=None):
    request = Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})}, method="POST",
    )
    try:
        with urlopen(request, timeout=45) as response:
            return json.load(response)
    except HTTPError as exc:
        exc.close()
        raise RuntimeError(f"{request.selector} returned HTTP {exc.code}") from None


def expect(test, description):
    if not test:
        raise AssertionError(description)


def check_storage(url):
    def post(endpoint, payload):
        return request_json(url + endpoint, payload)

    first = post("/load", {"op": "new"})["code"]
    second = post("/load", {"op": "new"})["code"]
    expect(first != second and SAVE_CODE.fullmatch(first) and SAVE_CODE.fullmatch(second), "unique save codes")
    initial = {"schema": 1, "clue": "", "ending": ""}
    open_request = {"code": first, "version": 0, "state": initial}
    expect(post("/load", {"op": "resume", "code": first})["status"] == "missing", "uncreated save is missing")
    expect(post("/save", open_request)["status"] == "saved", "opening save commits")
    expect(post("/save", open_request)["status"] == "saved", "identical retry is idempotent")
    first_state = post("/load", {"op": "resume", "code": first})
    expect(first_state == {"status": "loaded", "state": initial, "version": 1}, "revision one is readable")
    expect(post("/load", {"op": "resume", "code": second})["status"] == "missing", "second save is isolated")

    def race(clue):
        state = {"schema": 1, "clue": clue, "ending": ""}
        result = post("/save", {"code": first, "version": 1, "state": state})
        return clue, result["status"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = dict(pool.map(race, ["lens", "chart"]))
    expect(sorted(results.values()) == ["conflict", "saved"], "only one competing action commits")
    current = post("/load", {"op": "resume", "code": first})
    expect(current["version"] == 2 and results[current["state"]["clue"]] == "saved", "revision two matches the winner")
    print("PowerContext storage: initial save, idempotent retry, isolation, revision readback, concurrent update passed")


def check_dify(base, api_key, restart_memory=None):
    turns = []
    user = "fog-harbor-test-" + secrets.token_hex(8)

    def chat(query, conversation="", required=(), forbidden=(), label=""):
        payload = {
            "query": query, "inputs": {}, "response_mode": "blocking", "user": user,
            "auto_generate_name": False,
        }
        if conversation:
            payload["conversation_id"] = conversation
        result = request_json(base + "/v1/chat-messages", payload, {"Authorization": "Bearer " + api_key})
        answer = result.get("answer", "")
        missing = [word for word in required if word not in answer]
        unexpected = [word for word in forbidden if word in answer]
        sanitized_query = SAVE_CODE.sub("<存档码>", query)
        sanitized_answer = SAVE_CODE.sub("<存档码>", answer)
        expect(not missing and not unexpected,
               f"{sanitized_query!r}: missing={missing!r}, unexpected={unexpected!r}, answer={sanitized_answer!r}")
        turn = {"query": sanitized_query, "answer": sanitized_answer, "conversation_id": result["conversation_id"]}
        if label:
            turn["check"] = label
        turns.append(turn)
        return result["conversation_id"], answer

    def new_game():
        conversation, answer = chat("开始", required=("第一幕", "修灯", "问潮", "送信"))
        match = SAVE_CODE.search(answer)
        expect(match is not None, "opening displays a save code")
        return conversation, match.group()

    chat("帮助", required=("新存档",))
    chat("继续 H-" + secrets.token_hex(12).upper(), required=("没找到这份存档",), label="未知存档")

    routes = [
        ("修灯", "修好的红色透镜", "引航", "放船", "红光领航"),
        ("问潮", "阿禾画的退潮水线", "放船", "引航", "潮线渡口"),
        ("送信", "船闸值守员签回的急件", "开闸", "引航", "港灯接力"),
    ]
    for index, (preparation, keepsake, action, other_action, ending) in enumerate(routes):
        opened, code = new_game()
        chat(preparation, opened, required=("第二幕", keepsake, f"**{action}**"), forbidden=(f"**{other_action}**",))
        if index == 0 and restart_memory:
            restart_memory()
        resumed, _ = chat("继续 " + code, required=("已从值班簿恢复", "第二幕", keepsake, f"**{action}**"))
        expect(resumed != opened, "progress resumed in a genuinely different Dify conversation")
        chat(other_action, resumed, required=("眼下无法", "第二幕"))
        chat(preparation, resumed, required=("已经记进值班簿", "第二幕"))
        chat(action, resumed, required=("结局 · " + ending,))
        chat(code, required=("已从值班簿恢复", "结局 · " + ending))

    opened, _ = new_game()
    chat("问潮", opened, required=("第二幕", "退潮水线"))
    chat("鸣铃", opened, required=("结局 · 雾中候船", "下次退潮"))
    fresh, _ = new_game()
    chat("引航", fresh, required=("眼下无法", "第一幕"))
    return turns


def main():
    parser = argparse.ArgumentParser(description="Run the live Fog Harbor acceptance checks")
    parser.add_argument("--url", default="http://127.0.0.1:8088")
    parser.add_argument("--memory-only", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "acceptance.local.json")
    args = parser.parse_args()
    url = args.url.rstrip("/")
    try:
        if args.memory_only:
            check_storage(url)
            return
        api_key = os.getenv("DIFY_APP_API_KEY") or getpass.getpass("Dify app API key: ")
        expect(bool(api_key), "An app API key is required")
        results = check_dify(url, api_key)
        report = {"verified_at": datetime.now(timezone.utc).isoformat(), "turn_count": len(results), "turns": results}
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{len(results)} live Dify turns passed. Redacted transcript: {args.output}")
    except (OSError, ValueError, KeyError, AssertionError, RuntimeError) as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
