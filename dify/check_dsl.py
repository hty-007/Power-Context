import json
import re
from pathlib import Path

import yaml

from build_dsl import render_dsl


ROOT = Path(__file__).resolve().parent
CODE = "H-0123456789ABCDEF01234567"


def check_graph():
    raw = (ROOT / "fog-harbor.yml").read_text(encoding="utf-8")
    assert raw == render_dsl(), "Regenerate fog-harbor.yml with build_dsl.py"
    graph = yaml.safe_load(raw)["workflow"]["graph"]
    nodes = {node["id"]: node["data"] for node in graph["nodes"]}
    assert len(nodes) == len(graph["nodes"])
    reachable = {node_id for node_id, data in nodes.items() if data["type"] == "start"}
    assert len(reachable) == 1
    for edge in graph["edges"]:
        source, target = edge["source"], edge["target"]
        assert edge["data"]["sourceType"] == nodes[source]["type"]
        assert edge["data"]["targetType"] == nodes[target]["type"]
    for _ in nodes:
        reachable.update(edge["target"] for edge in graph["edges"] if edge["source"] in reachable)
    assert reachable == set(nodes)

    functions = {}
    for entry in nodes.values():
        if entry["type"] == "code":
            globals_for_node = {}
            exec(compile(entry["code"], entry["title"], "exec"), globals_for_node)
            functions[entry["title"]] = globals_for_node["main"]
        for node_id, variable in re.findall(r"\{\{#(\d+)\.(\w+)#\}\}", json.dumps(entry, ensure_ascii=False)):
            assert variable in nodes[node_id]["outputs"]
    return functions


def check_story(functions):
    parse = functions["识别指令"]
    scene = functions["推进剧情与解锁行动"]
    confirm = functions["确认写入结果"]

    def play(query, active_code="", memory=None):
        parsed = parse(query, active_code)
        return scene(parsed["op"], parsed["action"], parsed["code"], parsed["previous_code"], json.dumps(memory))

    def loaded(save, version):
        return {"status": "loaded", "version": version, "state": json.loads(save["save_body"])["state"]}

    assert play("帮助")["mode"] == "reply"
    assert "还没有可继续" in play("修灯")["answer"]
    assert "没找到" in play("继续 " + CODE, memory={"status": "missing"})["answer"]
    assert "暂时打不开" in play("继续 " + CODE, memory={"status": "unavailable"})["answer"]
    opening = play("开始", memory={"status": "new", "code": CODE})
    assert opening["mode"] == "save" and "第一幕" in opening["answer"]
    assert json.loads(opening["save_body"])["version"] == 0
    initial = loaded(opening, 1)
    assert "无法**引航**" in play("引航", CODE, initial)["answer"]
    assert "页码异常" in play("引航", CODE, dict(initial, version=2))["answer"]

    routes = [
        ("修灯", "引航", "放船", "红光领航", "红镜"),
        ("问潮", "放船", "开闸", "潮线渡口", "阿禾"),
        ("送信", "开闸", "引航", "港灯接力", "家属"),
    ]
    for move, final, wrong, ending, detail in routes:
        second = play(move, CODE, initial)
        assert second["mode"] == "save" and "第二幕" in second["answer"]
        assert json.loads(second["save_body"])["version"] == 1
        memory = loaded(second, 2)
        restored = play("继续 " + CODE, memory=memory)
        assert restored["active_code"] == CODE and restored["mode"] == "reply"
        assert "已从值班簿恢复" in restored["answer"] and f"**{final}**" in restored["answer"]
        assert f"**{wrong}**" not in restored["answer"]
        assert "眼下无法" in play(wrong, CODE, memory)["answer"]
        assert "已经记进" in play(move, CODE, memory)["answer"]
        completed = play(final, CODE, memory)
        assert completed["mode"] == "save" and "结局 · " + ending in completed["answer"]
        assert json.loads(completed["save_body"])["version"] == 2
        assert "已到结局" in play(move, CODE, loaded(completed, 3))["answer"]
        wait = play("鸣铃", CODE, memory)
        assert "结局 · 雾中候船" in wait["answer"] and detail in wait["answer"]

    ok = confirm('{"status":"saved"}', opening["answer"], CODE, "", "new")
    assert ok == {"answer": opening["answer"], "active_code": CODE}
    uncertain = confirm('{"status":"unavailable"}', opening["answer"], CODE, "", "move")
    assert "未收到写入确认" in uncertain["answer"] and uncertain["active_code"] == ""
    conflict = confirm('{"status":"conflict"}', opening["answer"], CODE, "", "move")
    assert "已有更新" in conflict["answer"]


if __name__ == "__main__":
    check_story(check_graph())
    print("DSL graph and embedded Python: all four endings, three memory-dependent choices, invalid inputs and save failures passed")
