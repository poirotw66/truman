"""劇本：嵐潮——暴潮來襲前的半日。

壯闊題材的和平箱庭：對手不是人，是海。六個人各有本事，目的咬在一起——
做海醮要人看醮、焊水門要人作證、疏散要人聽勸。導演只放天氣與潮訊，不替誰做決定。

勝負很清楚：亥時（tick 72）前若「做海醮」或「焊水門」沒做成，潮頭灌進低處，
淹水格子會真的斷路；站在水裡的人會被捲走。誰活著、誰趕到、誰說動了誰，才是戲。
"""

from __future__ import annotations

from truman.config import clock_str
from truman.world import arts as arts_mod
from truman.world.grid import Area, Grid, Pos
from truman.world.state import AgentState, Goal, WorldState

NAME = "tempest"
COMBAT = False  # 對手是海，不是刀；動手規則整層不掛

__all__ = [
    "AREAS", "GRID_ROWS", "LEGEND", "build_grid", "build_world", "after_goals",
    "BRIEF", "NORMS", "PUBLIC_CAST", "PUBLIC_LORE", "SETTING", "EXAMPLES",
    "AGENTS", "DIRECTOR_SCRIPT", "TRACK_TOPICS", "NAME", "COMBAT",
    "STORM_TICK", "SAFE_AREAS", "LOW_AREAS", "SOUTH_AREAS", "RENAMED",
]

# ---------------------------------------------------------------- 地圖
# 北高南低、略拉大：北三區避難，中間廣場與作坊，南貼雙排海。潮從南來，逃往北。
GRID_ROWS = [
    "############################",
    "#aaa......bbbb......cccc...#",
    "#aaa......bbbb......cccc...#",
    "#aaa......bbbb......cccc...#",
    "#..........................#",
    "#..........................#",
    "#.......dddddddddd.........#",
    "#.......dddddddddd.........#",
    "#.......dddddddddd.........#",
    "#..........................#",
    "#eeee........ffff....gggg..#",
    "#eeee........ffff....gggg..#",
    "#eeee........ffff....gggg..#",
    "#..........................#",
    "#hhhhhhhhhhhh....pppppppppp#",
    "#~~~~~~~~~~~~~~~~~~~~~~~~~~#",
    "#~~~~~~~~~~~~~~~~~~~~~~~~~~#",
    "############################",
]

LEGEND = {
    "#": ("山壁", False),
    ".": ("街道", True),
    "~": ("海", False),
    "a": ("高地岩面", True),
    "b": ("宅院", True),
    "c": ("廟宇石階", True),
    "d": ("廣場石板", True),
    "e": ("鐵鋪地面", True),
    "f": ("漁市泥地", True),
    "g": ("倉房", True),
    "h": ("海堤石面", True),
    "p": ("碼頭木板", True),
}

AREAS = [
    Area("高地", 1, 1, 3, 3, "鎮北最高處。風雨裡唯一能指望不被第一波潮水吞掉的地方。"),
    Area("村長宅", 9, 1, 12, 3, "林美華的宅子。堂屋掛著歷任村長的名冊，門總是開著。"),
    Area("鎮廟", 19, 1, 22, 3, "面海的舊廟。香灰厚，鐘鏽了，卻是全鎮信海神的地方。"),
    Area("廣場", 8, 6, 17, 8, "鎮心廣場。平時晒網、議事；暴潮前夜最容易擠成人潮。"),
    Area("鐵鋪", 1, 10, 4, 12, "張鐵雄的鋪子。爐火終年不熄，門口堆著錨鏈與閘板。"),
    Area("漁市", 13, 10, 16, 12, "早市散了還剩鹹腥味。潮訊一緊，這裡最先空。"),
    Area("糧倉", 21, 10, 24, 12, "公倉。木門沉重，裡面堆著度荒的粟。"),
    Area("海堤", 1, 14, 12, 14, "貼海的石堤，中段有水閘。封不住，潮就從這裡灌進來。"),
    Area("漁港", 17, 14, 26, 14, "木棧碼頭。船若沒及時回港，人與船都會被捲走。"),
]

# tick 72 = 第1天 18:00（與 jianghu 金盆時辰同一套鐘）
STORM_TICK = 72

# 結算用：禮成／閘封決定哪些低處會被淹。
SAFE_AREAS = ("高地", "鎮廟", "村長宅")  # 第一波到不了
SOUTH_AREAS = ("海堤", "漁港", "漁市")   # 閘沒封就先灌這裡
LOW_AREAS = ("海堤", "漁港", "漁市", "廣場", "鐵鋪", "糧倉")  # 兩樣都敗才全淹

BRIEF = f"""\
嵐潮鎮貼著海。北面是高地與鎮廟，南面是海堤和漁港，中間一條街穿過廣場。
鎮上不大，但從漁港跑到鎮廟要搶時間——暴潮來時，一刻鐘就決定生死。

今天不是普通的壞天氣。老輩人認得那種雲：潮會在今天傍晚前後上岸。
若亥時之前（約第 {STORM_TICK} 拍）鎮廟的做海醮沒做成、海堤水閘沒封上，
低處會真的被水吞掉——路斷、人捲走，那就是滅村。

哪裡做什麼，大家心裡都該有數：

- **鎮廟**：做海醮。要有人在場看醮，空廟不算。
- **海堤**：焊水門。要人在旁邊作證，一個人悄悄焊死也不被承認。
- **高地**：疏散的去處。帶不動人，醮與水門都救不了擠在低處的人。
- **漁港**：叫船回來。船若還在外海，醮成了也會死人。
- **廣場**：消息與恐慌都從這裡擴散。
- **鐵鋪／漁市／村長宅／糧倉**：備料、勸人、取物，各有用處。
"""

NORMS = """\
嵐潮人講話硬、做事搶。危急時更是如此——先做，再說對不起。

對話通常很短。暴潮前夜沒人有功夫站著聊天。
答應了的事，海會記得；忘了的事，潮不會等人。

你不必客套，但完全不理人會讓疏散卡住。
叫人去鎮廟、去海堤、上高地，是今天最有用的話。
"""

PUBLIC_CAST = """\
- 陳金水，鎮廟廟公，五十多歲。懂舊禮，手穩，信海神也信人要自己走到場上。
- 張鐵雄，鐵匠，四十歲。臂力驚人，話少，覺得閘比禱詞可靠。
- 林美華，村長，三十八歲。負責把人趕到該去的地方——勸不動也得勸。
- 阿海，漁民，二十九歲。識潮，船班多半聽他的。
- 黃秀英，草藥醫，三十三歲。穩得住慌的人，也知道誰該先上高地。
- 阿德，守望人，四十五歲。常年在海堤看潮色，腿快、眼尖。
"""

PUBLIC_LORE = """\
# 嵐潮鎮的舊規矩

- 暴潮不是每年都有，但來了就是滅村的量級。上一次，鎮南三排屋子沒了。
- **做海醮**必須在鎮廟當眾做；海神不認偷偷摸摸的禱告。
- **焊水門**是人對海的工程。禮與閘，少一樣都可能崩。
- 高地夠高，第一波到不了——但路窄，人一擠就踩踏。
- 漁船若在外海遇上暴潮，岸上的人只能點燈，救不回來。
- 亥時一到，沒封住的低處會被潮水實實在在灌進來；站在水裡的人，海不留情。
"""

SETTING = """\
你在嵐潮鎮。今天傍晚前後暴潮會上岸。
你的對手是海，不是某個要殺你的人。刀劍解決不了潮水。

你有明確要做成的事，也有趁手的本事（絕技）。
本事有次數、有距離、有場合——用錯了地方等於沒用。
旁人會不會來幫你看醮、作證、撤走，是你要用嘴和腳去爭取的。
亥時一過，沒守住的低處真的會被水淹斷——別賭海會手下留情。
"""

EXAMPLES = """\
情況：你是廟公，潮訊已緊，鎮廟裡只有你一個人。
  thought: 空廟做醮不算。得把人叫來，至少兩個。
  action: speak / target_agent=林美華 / utterance=村長，帶兩個人上廟，我現在就起醮。

情況：你是鐵匠，人已在海堤，旁邊有守望人。
  thought: 有人看見就能焊水門。再拖，潮頭會先到。
  action: use_art / art=焊水門

情況：你是村長，廣場上有人發慌想往海堤看熱鬧。
  thought: 往南是送死。得把人趕上高地或鎮廟。
  action: use_art / art=派工 / target_agent=阿海 / utterance=你先去漁港收船，收完上廟看醮。
"""

# ---------------------------------------------------------------- 人物
#
# 目的彼此咬合：廟公要看醮者、鐵匠要證人、村長要動員、漁民要收船、
# 醫者要護人上高地、守望要先把潮訊送到人眼前。

AGENTS = [
    {
        "id": "shen_xi",
        "name": "陳金水",
        "role": "villager",
        "home_area": "鎮廟",
        "start": (20, 2),
        "arts": ["zhen_chao_li", "an_min_zhou"],
        "goals": [
            {"kind": "ritual", "text": "在鎮廟當眾做成海醮，壓住暴潮",
             "params": {"rite": "做海醮", "by_tick": STORM_TICK}},
        ],
        "persona": """\
你叫陳金水，嵐潮鎮廟的廟公，五十多歲。大家還叫你金水伯。
你信海神，但更信「禮要人看見」——空廟裡的禱告海不認。

今天傍晚暴潮會上岸。你今天只要做成一件事：在鎮廟當眾做完這場海醮。
至少要有兩個人在場看醮。醮成之前，你什麼都可以放下；醮敗了，庄就完了。
你可以安撫慌亂的人，但安撫代替不了起醮。""",
    },
    {
        "id": "shi_lei",
        "name": "張鐵雄",
        "role": "villager",
        "home_area": "鐵鋪",
        "start": (2, 11),
        "arts": ["feng_zha", "ji_feng_bu"],
        "goals": [
            {"kind": "ritual", "text": "在海堤當眾封死水閘",
             "params": {"rite": "焊水門", "by_tick": STORM_TICK}},
        ],
        "persona": """\
你叫張鐵雄，鐵匠，四十歲，臂力比話多。庄裡人都喊你鐵雄。
你覺得禱詞救不了閘——閘要焊、要頂、要人看見你封上了。

今天你必須在海堤把水閘封死，而且旁邊要有人能作證。
封晚了，做海醮也擋不住灌進來的水。你跑得動，別浪費在廣場站著聽人哭。""",
    },
    {
        "id": "fang_lan",
        "name": "林美華",
        "role": "villager",
        "home_area": "村長宅",
        "start": (10, 2),
        "arts": ["hao_ling", "an_min_zhou"],
        "goals": [
            {"kind": "meet", "text": "當面把陳金水說動／對上話，確認起醮",
             "params": {"who": "shen_xi"}},
            {"kind": "meet", "text": "當面把張鐵雄說動／對上話，確認焊水門",
             "params": {"who": "shi_lei"}},
            {"kind": "protect", "text": "盡力護住鎮上這些還能走的人活過今天",
             "params": {"who": ["shen_xi", "shi_lei", "a_qian", "qing_he", "gu_chao"]}},
        ],
        "persona": """\
你叫林美華，嵐潮村長，三十八歲。庄裡人都叫你美華姐。
暴潮面前，村長的工作不是哭，是把人趕到該去的地方：鎮廟看醮、海堤作證、高地躲。

你今天要親自跟陳金水、張鐵雄對上話，確認禮與閘都有人做。
你可以用派工把人派出去，也可以安撫廣場上的恐慌——但你勸不動海，只能勸人。""",
    },
    {
        "id": "a_qian",
        "name": "阿海",
        "role": "villager",
        "home_area": "漁港",
        "start": (22, 11),  # 從糧倉出發，得自己跑回港
        "arts": ["ji_feng_bu", "wang_chao"],
        "goals": [
            {"kind": "reach", "text": "先回到漁港，把還在外面的船班叫回來",
             "params": {"area": "漁港"}},
            {"kind": "meet", "text": "找到阿德，對上潮訊",
             "params": {"who": "gu_chao"}},
        ],
        "persona": """\
你叫阿海，漁民，二十九歲，識潮。全庄叫你阿海就好。
船還在外海，岸上的禮再盛大也救不了人。你得先回漁港收船、放訊號。

你腿快，也能探潮打聽誰在哪。收完船，去跟阿德對一下潮訊——他看海堤，你看船路。""",
    },
    {
        "id": "qing_he",
        "name": "黃秀英",
        "role": "villager",
        "home_area": "漁市",
        "start": (14, 11),
        "arts": ["an_min_zhou", "hao_ling"],
        "goals": [
            {"kind": "reach", "text": "自己先撤到高地，做疏散的節點",
             "params": {"area": "高地"}},
            {"kind": "meet", "text": "把林美華找到，確認誰該先撤",
             "params": {"who": "fang_lan"}},
        ],
        "persona": """\
你叫黃秀英，草藥醫，三十三歲。庄裡看病抓藥都找你。
你知道慌比潮先死人：人一擠，高地的窄路會踩踏。

你今天要自己先上高地站住，並找到村長林美華，把「誰先撤、誰去看醮」說清楚。
你能安撫人，也能派工——用在刀口上，別浪費在跟潮水吵架。""",
    },
    {
        "id": "gu_chao",
        "name": "阿德",
        "role": "villager",
        "home_area": "海堤",
        "start": (12, 7),  # 從廣場出發，得自己赶回海堤
        "arts": ["wang_chao", "ji_feng_bu"],
        "goals": [
            {"kind": "reach", "text": "守在海堤，確認閘還能不能封",
             "params": {"area": "海堤"}},
            {"kind": "meet", "text": "找到張鐵雄，讓他來焊水門",
             "params": {"who": "shi_lei"}},
        ],
        "persona": """\
你叫阿德，守望人，四十五歲，常年蹲在海堤看雲色與潮聲。大家都只叫你阿德。
你今天的工作是守住海堤視線，並把張鐵雄找回來焊水門——你焊不動閘，但你跑得過風。

你能探潮打探別人在哪，也能飛毛腿趕路。別在廣場聽人哭，海堤才是你的崗。""",
    },
]

# 舊名 → 現名。角色在 commit e6d526c 改成了現在這套台灣海線名字，
# 但 runs/t1、runs/t2 這兩場是改名前跑的：events.jsonl 裡的台詞、旁白、
# reflection 全部留著舊名——那是實跑紀錄，是史料，不能回頭改日誌本身。
# 這張表只給回放頁（replay/build_frames.py）在「顯示」時把日誌文字換成
# 現在的名字用，不影響任何模擬邏輯，也不影響日誌檔案本身一個字。
RENAMED = {
    "沈汐": "陳金水",
    "石磊": "張鐵雄",
    "方嵐": "林美華",
    "阿潛": "阿海",
    "青禾": "黃秀英",
    "顧潮": "阿德",
}

# ---------------------------------------------------------------- 世界事件：只放潮，不替人做決定
DIRECTOR_SCRIPT = [
    {
        "tick": 8,
        "kind": "broadcast",
        "area": "廣場",
        "text": "（北邊雲牆壓下來，海鳥成群往內陸飛。有人低聲說：這種雲，上回來過一次。）",
    },
    {
        "tick": 24,
        "kind": "broadcast",
        "area": "海堤",
        "text": "（潮聲變了。不是拍岸，是遠雷似的一聲接一聲。水閘縫裡開始往外冒白沫。）",
    },
    {
        "tick": 36,
        "kind": "broadcast",
        "area": "漁港",
        "text": "（外海一道白線平行岸邊推近。碼頭木頭咯吱響，沒回港的船看不見影。）",
    },
    {
        "tick": 48,
        "kind": "broadcast",
        "area": "廣場",
        "text": "（風把招牌掀下來。有人往南跑去看潮，有人往北擠。廣場亂了。）",
    },
    {
        "tick": 60,
        "kind": "broadcast",
        "area": "鎮廟",
        "text": "（廟裡銅鈴自己響了一下。香灰被風吹成一條線，指著海的方向。）",
    },
    {
        "tick": 68,
        "kind": "broadcast",
        "area": "海堤",
        "text": "（第一排浪打過堤面，沒過腳踝。再拖，下一排會過膝——亥時之前閘還封得上。）",
    },
    {
        "tick": STORM_TICK,
        "kind": "broadcast",
        "area": None,
        "text": "（亥時到了。暴潮主峰抵岸——禮與閘成不成，這一刻見真章。）",
    },
    {
        "tick": 84,
        "kind": "broadcast",
        "area": "高地",
        "text": "（風勢略緩。低處若還亮著燈，是人守住了；若只剩一片反光，那是臨時的海。）",
    },
]

TRACK_TOPICS = ["暴潮", "海醮", "焊水門", "海堤", "高地", "漁港", "疏散", "看醮", "滅村"]


def build_grid() -> Grid:
    return Grid(
        GRID_ROWS, LEGEND, AREAS, street="街道",
        aliases={
            "廟": "鎮廟",
            "海神廟": "鎮廟",
            "碼頭": "漁港",
            "港": "漁港",
            "水閘": "海堤",
            "堤": "海堤",
            "避難所": "高地",
            "山上": "高地",
            "鐵匠鋪": "鐵鋪",
            "市場": "漁市",
        },
    )


def build_world(run_id: str, seed: int) -> WorldState:
    world = WorldState(run_id=run_id, scenario=NAME, seed=seed, tick=0)
    for spec in AGENTS:
        world.agents[spec["id"]] = AgentState(
            id=spec["id"],
            name=spec["name"],
            role=spec["role"],
            persona=spec["persona"],
            home_area=spec["home_area"],
            pos=Pos(*spec["start"]),
            goals=[Goal.from_dict(g) for g in spec.get("goals", [])],
            arts=arts_mod.equip(spec.get("arts", [])),
        )
    return world


# ---------------------------------------------------------------- 暴潮結算
def _rite_done(world: WorldState, agent_id: str, rite: str) -> bool:
    a = world.agents.get(agent_id)
    if a is None:
        return False
    return any(
        g.kind == "ritual" and g.params.get("rite") == rite and g.status == "done"
        for g in a.goals
    )


def _announce(engine, text: str) -> None:
    """全鎮旁白：寫進日誌給 demo，並塞進下一拍每個人的眼前。"""
    w, t = engine.world, engine.world.tick
    when = clock_str(t)
    engine.log.write(
        "director",
        {"kind": "broadcast", "area": None, "text": text, "fired": True, "tag": "storm"},
    )
    for a in w.agents.values():
        if a.alive:
            a.memory.add(t, when, "observation", text, importance=10)
        if engine.director is not None:
            engine.director.add_runtime(a.id, text, t + 1, tag="storm")
    if engine.console:
        engine.console.print(f"[bold cyan]≈ {text}[/bold cyan]")


def _drown(engine, agent) -> None:
    """被暴潮捲走。不走動手規則，直接標死。"""
    if not agent.alive:
        return
    w, t = engine.world, engine.world.tick
    when = clock_str(t)
    agent.wound = 3
    agent.killed_by = "暴潮"
    agent.action = {"kind": "wait", "ticks_left": 1, "done": False}
    engine._signals.deaths.append(agent.id)
    line = f"{agent.name}被暴潮捲進水裡，再沒上來。"
    engine.log.write(
        "death",
        {"agent": agent.id, "name": agent.name, "killed_by": "暴潮", "when": when, "line": line},
    )
    for other in w.agents.values():
        if other is agent:
            continue
        if other.pos.chebyshev(agent.pos) <= engine.cfg.vision_radius:
            other.memory.add(t, when, "observation", line, importance=9)
            other.action = None
    if engine.console:
        engine.console.print(f"[red]≈ {line}[/red]")


def after_goals(engine) -> None:
    """亥時結算：依禮／閘決定淹哪些區、鎮是守住還是滅村邊緣。

    掛在 Engine.on_after_goals——目的判定之後、reflect 之前。
    只跑一次（world.outcome 非空就跳過）。
    """
    w = engine.world
    if w.tick != STORM_TICK or w.outcome:
        return

    rite = _rite_done(w, "shen_xi", "做海醮")
    sealed = _rite_done(w, "shi_lei", "焊水門")
    grid = engine.grid

    if rite and sealed:
        w.outcome = "held"
        flood_names: list[str] = []
        text = (
            "（海醮做成了，水門焊死。主峰撞上堤面又退回去——嵐潮庄，守住了。）"
        )
    elif sealed and not rite:
        w.outcome = "partial"
        flood_names = []  # 水門住了，醮未成：人驚、庄還在
        text = (
            "（水門焊上了，主峰沒灌進庄子。可海醮沒做成——"
            "海神沒點頭，人心比潮還沉。嵐潮算半個僥倖。）"
        )
    elif rite and not sealed:
        w.outcome = "partial"
        flood_names = list(SOUTH_AREAS)
        text = (
            "（海醮做成了，潮頭矮了一截，可水門沒焊——"
            "海堤、漁港、漁市還是被灌成了臨時的海。嵐潮，只救回了一半。）"
        )
    else:
        w.outcome = "lost"
        flood_names = list(LOW_AREAS)
        text = (
            "（醮未成，水門未焊。暴潮灌進低處，路斷、燈滅——"
            "嵐潮庄，走到了滅村的邊緣。）"
        )

    # 全敗時連貼海那排街道一併淹，斷掉往北逃的窄路。
    cells = grid.cells_in_areas(flood_names)
    if w.outcome == "lost":
        for y in (13, 14):
            for x in range(grid.w):
                p = Pos(x, y)
                if (
                    grid.in_bounds(p)
                    and grid.legend[grid.symbol(p)][1]
                    and grid.area_at(p) not in SAFE_AREAS
                ):
                    cells.append(p)

    n_new = grid.flood_positions(cells)
    w.flooded = [p.as_list() for p in sorted(grid.flooded, key=lambda q: (q.y, q.x))]

    drowned = []
    for a in list(w.agents.values()):
        if a.alive and grid.is_flooded(a.pos):
            _drown(engine, a)
            drowned.append(a.name)

    w.outcome_text = text
    detail = {
        "outcome": w.outcome,
        "rite": rite,
        "sealed": sealed,
        "flooded_areas": flood_names,
        "flooded_cells": len(set(cells)),
        "flooded": w.flooded,
        "drowned": drowned,
        "text": text,
    }
    engine.log.write("storm", detail)
    _announce(engine, text)
    if drowned and engine.console:
        engine.console.print(f"[red]≈ 被潮捲走：{'、'.join(drowned)}[/red]")
