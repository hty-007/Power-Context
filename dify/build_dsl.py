from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent
START, PARSE, LOAD, SCENE, CHOICE = map(str, range(1710000000001, 1710000000006))
SAVE, CONFIRM, SET_SAVE, ANSWER_SAVE, SET_READ, ANSWER_READ = map(str, range(1710000000006, 1710000000012))


class Literal(str):
    pass


class DslDumper(yaml.SafeDumper):
    pass


DslDumper.add_representer(
    Literal, lambda dumper, value: dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|")
)


def reference(node, variable):
    return "{{#" + node + "." + variable + "#}}"


def node(node_id, kind, title, x, y, *, desc="", **data):
    position = {"x": x, "y": y}
    return {
        "id": node_id,
        "type": "custom",
        "data": {"type": kind, "title": title, "desc": desc, "selected": False, **data},
        "position": position.copy(),
        "positionAbsolute": position.copy(),
        "sourcePosition": "right",
        "targetPosition": "left",
        "width": 244,
        "height": 100,
        "selected": False,
    }


def code(node_id, title, x, y, script, inputs, outputs, desc):
    return node(
        node_id, "code", title, x, y, desc=desc,
        code_language="python3",
        code=Literal((ROOT / "nodes" / (script + ".py")).read_text(encoding="utf-8")),
        variables=[{"variable": name, "value_selector": selector} for name, selector in inputs.items()],
        outputs={name: {"type": "string", "children": None} for name in outputs},
    )


def http(node_id, title, x, y, path, body, desc):
    return node(
        node_id, "http-request", title, x, y, desc=desc,
        method="post", url=f"http://story-memory:8079/{path}",
        authorization={"type": "no-auth", "config": None},
        headers="Content-Type:application/json", params="", variables=[],
        body={"type": "json", "data": [{"id": node_id + "-body", "key": "", "value": body, "type": "text"}]},
        ssl_verify=True, timeout={"connect": 5, "read": 20, "write": 5},
        retry_config={"retry_enabled": False, "max_retries": 0, "retry_interval": 100},
        error_strategy="default-value",
        default_value=[
            {"key": "body", "type": "string", "value": '{"status":"unavailable"}'},
            {"key": "status_code", "type": "number", "value": 503},
            {"key": "headers", "type": "object", "value": {}},
            {"key": "files", "type": "array[file]", "value": []},
        ],
    )


def assign(node_id, x, y, source):
    return node(
        node_id, "assigner", "记住当前存档码", x, y,
        desc="用于在同一对话中直接发送行动词。",
        version="2", items=[{
            "variable_selector": ["conversation", "active_code"],
            "input_type": "variable", "operation": "over-write", "value": [source, "active_code"],
        }],
    )


def build():
    nodes = [
        node(START, "start", "接收玩家消息", 0, 0, variables=[]),
        code(PARSE, "识别指令", 310, 0, "parse", {
            "query": ["sys", "query"], "active_code": ["conversation", "active_code"],
        }, ["op", "action", "code", "previous_code", "load_body"], "识别开始、恢复或行动，准备存档请求。"),
        http(LOAD, "读取 PowerContext 存档", 620, 0, "load", reference(PARSE, "load_body"),
             "恢复和行动都会按存档码读当前 Atomic Memory。"),
        code(SCENE, "推进剧情与解锁行动", 930, 0, "scene", {
            "op": [PARSE, "op"], "action": [PARSE, "action"], "code": [PARSE, "code"],
            "previous_code": [PARSE, "previous_code"], "loaded": [LOAD, "body"],
        }, ["mode", "answer", "active_code", "save_body"], "由已读回的线索决定选项和结局。"),
        node(CHOICE, "if-else", "需要写入新进度吗？", 1240, 0, cases=[{
            "case_id": "true", "logical_operator": "and", "conditions": [{
                "id": "fog-harbor-save-condition", "varType": "string", "variable_selector": [SCENE, "mode"],
                "comparison_operator": "is", "value": "save",
            }],
        }]),
        http(SAVE, "写入 PowerContext 存档", 1550, -150, "save", reference(SCENE, "save_body"),
             "把开档或选择写为 Atomic Memory 的新修订。"),
        code(CONFIRM, "确认写入结果", 1860, -150, "confirm", {
            "saved": [SAVE, "body"], "answer": [SCENE, "answer"], "new_code": [SCENE, "active_code"],
            "previous_code": [PARSE, "previous_code"], "op": [PARSE, "op"],
        }, ["answer", "active_code"], "收到写入确认后，才向玩家展示新进度。"),
        assign(SET_SAVE, 2170, -150, CONFIRM),
        node(ANSWER_SAVE, "answer", "返回写入结果", 2480, -150, answer=reference(CONFIRM, "answer"), variables=[]),
        assign(SET_READ, 1550, 160, SCENE),
        node(ANSWER_READ, "answer", "返回当前剧情或帮助", 1860, 160, answer=reference(SCENE, "answer"), variables=[]),
    ]
    by_id = {entry["id"]: entry["data"]["type"] for entry in nodes}
    edges = []
    for source, handle, target in [
        (START, "source", PARSE), (PARSE, "source", LOAD), (LOAD, "source", SCENE),
        (SCENE, "source", CHOICE), (CHOICE, "true", SAVE), (SAVE, "source", CONFIRM),
        (CONFIRM, "source", SET_SAVE), (SET_SAVE, "source", ANSWER_SAVE),
        (CHOICE, "false", SET_READ), (SET_READ, "source", ANSWER_READ),
    ]:
        edges.append({
            "id": f"{source}-{handle}-{target}-target", "type": "custom",
            "source": source, "sourceHandle": handle, "target": target, "targetHandle": "target",
            "data": {"sourceType": by_id[source], "targetType": by_id[target], "isInIteration": False, "isInLoop": False},
            "zIndex": 0,
        })
    return {
        "app": {
            "name": "雾港留灯", "mode": "advanced-chat", "icon": "🏮", "icon_background": "#FFEAD5",
            "description": "轮渡即将驶进雾港。你只有一幕准备时间，而值班簿会记住你的选择。",
            "use_icon_as_answer_icon": True,
        },
        "dependencies": [], "kind": "app", "version": "0.6.0",
        "workflow": {
            "conversation_variables": [{
                "id": "56fdf487-4a2c-4b2a-8fd8-57d1353112f7", "name": "active_code",
                "description": "当前对话选用的存档码", "selector": ["conversation", "active_code"],
                "value_type": "string", "value": "",
            }],
            "environment_variables": [],
            "features": {
                "opening_statement": Literal(
                    "雾港的引航灯灭了。最后一班轮渡还在海上。\n\n"
                    "发送 **开始** 接班，或发送 `继续 存档码` 读回值班簿。\n"
                ),
                "suggested_questions": ["开始", "怎么玩"],
                "suggested_questions_after_answer": {"enabled": False},
                "speech_to_text": {"enabled": False}, "text_to_speech": {"enabled": False},
                "retriever_resource": {"enabled": False}, "sensitive_word_avoidance": {"enabled": False},
                "file_upload": {"enabled": False, "image": {"enabled": False}},
            },
            "graph": {"nodes": nodes, "edges": edges, "viewport": {"x": 50, "y": 220, "zoom": 0.55}},
        },
    }


def render_dsl():
    return yaml.dump(build(), Dumper=DslDumper, allow_unicode=True, sort_keys=False, width=110)


if __name__ == "__main__":
    (ROOT / "fog-harbor.yml").write_text(render_dsl(), encoding="utf-8", newline="\n")
