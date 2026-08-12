"""Structured outputs 的 JSON schema。

刻意全部用 string，用空字串表示「無」——
structured outputs 不支援 minLength/maximum 之類的約束，
而 type union（["string","null"]）在各家 schema 編譯器上行為不一致，
用空字串約定的失敗模式最少。
"""

import copy

ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "thought": {
            "type": "string",
            "description": "你此刻的內心話，一到兩句。誠實，不用修飾。",
        },
        "action": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["move_to", "speak", "interact", "wait"],
                },
                "target_area": {
                    "type": "string",
                    "description": "kind=move_to 時填目標區域名，其餘填空字串。",
                },
                "target_agent": {
                    "type": "string",
                    "description": "kind=speak 時填對象的名字；對全場說話或其餘情況填空字串。",
                },
                "utterance": {
                    "type": "string",
                    "description": "kind=speak 時填你要說的話，其餘填空字串。",
                },
                "object": {
                    "type": "string",
                    "description": "kind=interact 時填你要做的事，例如「泡咖啡」，其餘填空字串。",
                },
                "art": {
                    "type": "string",
                    "description": "kind=use_art 時填絕技的名字，其餘填空字串。",
                },
            },
            "required": [
                "kind", "target_area", "target_agent", "utterance", "object", "art",
            ],
            "additionalProperties": False,
        },
        "plan": {
            "type": "string",
            "description": "接下來一兩個小時的打算，一句話。",
        },
    },
    "required": ["thought", "action", "plan"],
    "additionalProperties": False,
}


def action_schema(combat: bool = False, arts: bool = False) -> dict:
    """combat 劇本才把 attack 放進 enum；身上有絕技的人才看得到 use_art。

    和平劇本連這個選項都不該存在——enum 是模型看得到的東西，
    列出來就等於在暗示可以用。同理，沒配絕技的人不該看到 use_art：
    他每個 tick 都會多考慮一個永遠會被駁回的選項。
    """
    if not combat and not arts:
        return ACTION_SCHEMA
    s = copy.deepcopy(ACTION_SCHEMA)
    act = s["properties"]["action"]["properties"]
    extra = []
    if combat:
        extra.append("attack")
    if arts:
        extra.append("use_art")
    act["kind"]["enum"] = [*act["kind"]["enum"], *extra]

    who = ["speak"]
    if combat:
        who.append("attack")
    if arts:
        who.append("use_art（有些絕技要指定對象）")
    act["target_agent"]["description"] = (
        f"kind={' 或 '.join(who)} 時填對象的名字；對全場說話或其餘情況填空字串。"
    )
    if arts:
        act["utterance"]["description"] = (
            "kind=speak 時填你要說的話；"
            "kind=use_art 且那門絕技是靠說話起作用的（例如當眾指證、勸說），"
            "也要填你實際說出口的那句話。其餘填空字串。"
        )
    return s


REFLECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "insights": {
            "type": "array",
            "items": {"type": "string"},
            "description": "3–5 條從最近經歷歸納出的高階判斷。要具體、可被後續經驗推翻。",
        },
        "beliefs": {
            "type": "array",
            "items": {"type": "string"},
            "description": "更新後的長期信念清單（覆蓋舊的，最多 12 條）。",
        },
    },
    "required": ["insights", "beliefs"],
    "additionalProperties": False,
}


AWARENESS_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {
            "type": "integer",
            "enum": list(range(0, 11)),
            "description": "0 = 完全沒有懷疑，10 = 明確認定整個世界是為他安排的。",
        },
        "evidence": {
            "type": "array",
            "items": {"type": "string"},
            "description": "支持這個評分的原文片段。沒有就給空陣列。",
        },
        "rationale": {"type": "string"},
    },
    "required": ["score", "evidence", "rationale"],
    "additionalProperties": False,
}
