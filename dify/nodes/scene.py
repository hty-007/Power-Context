import json


CLUES = {
    "lens": {
        "action": "修灯",
        "keepsake": "修好的红色透镜",
        "recap": "你拆下裂镜时，在镜框背面摸到了三短一长四道刻痕。师傅教过你，那是轮渡认岸的灯号。",
        "choice": "引航",
        "option": "用红色透镜打出三短一长，把船引到东堤。",
        "ending": "红光领航",
        "result": "三道短光切开浓雾，第四道长光停在东堤。轮渡回了四声汽笛，慢慢转过暗礁。第一个上岸的孩子问那是不是星星。你把修好的红镜递到他面前，说是人留的灯。",
        "wait": "那枚红镜留在窗台，天亮时把第一束日光投进值班室。",
    },
    "chart": {
        "action": "问潮",
        "keepsake": "阿禾画的退潮水线",
        "recap": "舢板船主阿禾用沾盐的手在报纸上画出西汊浅滩。她答应听见双铃就放船，带你从轮渡侧面靠上去。",
        "choice": "放船",
        "option": "敲双铃叫上阿禾，沿她标出的退潮水线接人。",
        "ending": "潮线渡口",
        "result": "双铃响过，阿禾已经解开缆绳。你沿着那张报纸上的弯线领她绕过浅滩。船分三趟把旅客带回来。最后一趟，阿禾把湿透的报纸夹进舵缝，说下次还照这个走。",
        "wait": "阿禾把那张报纸晾在舱口，等下次退潮再补上今天的水线。",
    },
    "seal": {
        "action": "送信",
        "keepsake": "船闸值守员签回的急件",
        "recap": "你把迟到的轮渡名单挨家送到家属手上。最后一户正是值守员的家。她在急件上盖章，约定听到港灯接力的信号就放开内闸。",
        "choice": "开闸",
        "option": "凭签章请值守员打开内闸，让岸边家属用提灯标出水道。",
        "ending": "港灯接力",
        "result": "内闸慢慢抬起。收到名单的家属沿水道一盏盏提起灯，把船领进平静的内港。邮局窗口排起队，有人写地址，有人只写一句‘已到’，托你在天亮前寄出。",
        "wait": "家属把提灯挪到等候棚里，你替他们逐个念着名单。",
    },
}
FIRST = {clue["action"]: key for key, clue in CLUES.items()}
LOCKED = {clue["choice"]: clue["keepsake"] for clue in CLUES.values()}
HELP = (
    "发送 **开始** 开一份新存档。按回复里的加粗词发送行动，回复会给出接下来的选项。\n\n"
    "已有存档时，在新对话发送 `继续 存档码`，也可以直接粘贴存档码。"
)


def state_for(clue="", ending=""):
    return {"schema": 1, "clue": clue, "ending": ending}


def load_json(raw):
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def is_story_state(state, version):
    if not isinstance(state, dict) or state.get("schema") != 1:
        return False
    clue, ending = state.get("clue"), state.get("ending")
    if not isinstance(clue, str) or not isinstance(ending, str):
        return False
    if version == 1:
        return clue == "" and ending == ""
    if clue not in CLUES:
        return False
    if version == 2:
        return ending == ""
    return version == 3 and ending in {CLUES[clue]["choice"], "鸣铃"}


def render(state, code, notice=""):
    clue = CLUES.get(state["clue"])
    ending = state["ending"]
    if ending:
        name = "雾中候船" if ending == "鸣铃" else clue["ending"]
        story = clue["result"]
        if ending == "鸣铃":
            story = (
                "你敲响停航铃。轮渡在外锚地抛锚，等天亮再进港。邮局的火炉烧了一夜，"
                "来等人的家属把最后一壶茶留给你。" + clue["wait"]
            )
        content = (
            f"### 结局 · {name}\n\n{story}\n\n"
            f"**值班簿** · 你先{clue['action']}，后来选择了{ending}。\n\n"
            "发送 **开始** 可以换一条路。"
        )
    elif clue:
        content = (
            "### 第二幕 · 船已到雾里\n\n"
            f"{clue['recap']}\n\n"
            "外海传来一声短笛。轮渡已经到了暗礁外，值班钟只剩一次报时。\n\n"
            f"**值班簿** · 你先{clue['action']}，带来了{clue['keepsake']}。\n\n"
            f"- **{clue['choice']}** · {clue['option']}\n"
            "- **鸣铃** · 通知轮渡在外锚地过夜，等能见度恢复。\n\n"
            "发送加粗的行动词。"
        )
    else:
        content = (
            "### 第一幕 · 最后一班轮渡\n\n"
            "雾港邮局的最后一只挂钟走到十一点半。带着七名旅客的轮渡还在外海，"
            "礁石边的引航灯却灭了。你是今晚的值班人，离下一声汽笛只够办一件事。\n\n"
            "- **修灯** · 爬上东堤灯塔，翻开师傅留下的红镜箱。\n"
            "- **问潮** · 到西汊找船主阿禾，问她现在还能走哪条水路。\n"
            "- **送信** · 趁巷口还看得清门牌，把轮渡名单和急件交到岸上。\n\n"
            "发送加粗的行动词。"
        )
    if notice:
        content = notice + "\n\n" + content
    return content + f"\n\n**存档码** · `{code}`\n\n换一个对话时，发送 `继续 {code}`。"


def main(op: str, action: str, code: str, previous_code: str, loaded: str) -> dict:
    def reply(answer, active_code=previous_code):
        return {"mode": "reply", "answer": answer, "active_code": active_code, "save_body": ""}

    def save(state, version, code):
        return {
            "mode": "save",
            "answer": render(state, code),
            "active_code": code,
            "save_body": json.dumps(
                {"code": code, "version": version, "state": state},
                separators=(",", ":"),
            ),
        }

    if op in {"help", "unknown", "need_code"}:
        message = {
            "help": "",
            "unknown": "这条消息没匹配到行动。可以发送当前剧情中的加粗词。\n\n",
            "need_code": "这段对话还没有可继续的存档。\n\n",
        }[op]
        return reply(message + HELP)

    result = load_json(loaded)
    status = result.get("status")
    if status == "missing":
        return reply("没找到这份存档。请检查存档码，或发送 **开始** 开一份新存档。")
    if op == "new" and status == "new":
        return save(state_for(), 0, result["code"])
    if status != "loaded":
        return reply("值班簿暂时打不开。稍等片刻后重发刚才的消息。")

    state, version = result.get("state"), result.get("version")
    if not is_story_state(state, version):
        return reply("这份值班簿的页码异常。原存档已保留。请稍后重试或用 **开始** 开新档。")
    if op == "resume":
        return reply(render(state, code, "已从值班簿恢复上次进度。"), code)

    clue, ending = state["clue"], state["ending"]
    if ending:
        return reply(render(state, code, "这份存档已到结局。"), code)
    if not clue and action in FIRST:
        return save(state_for(FIRST[action]), version, code)
    if clue and action in {CLUES[clue]["choice"], "鸣铃"}:
        return save(state_for(clue, action), version, code)
    if clue and action == CLUES[clue]["action"]:
        return reply(render(state, code, f"**{action}**已经记进值班簿。"), code)
    notice = "这一幕请从下方选项中选择。"
    if action in LOCKED:
        notice = f"眼下无法**{action}**。这条路线需要{LOCKED[action]}。"
    return reply(render(state, code, notice), code)
