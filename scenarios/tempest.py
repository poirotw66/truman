"""劇本：嵐潮——暴潮來襲前的半日。

壯闊題材的和平箱庭：對手不是人，是海。七個人各有本事，目的咬在一起——
做海醮要人看醮、焊水門要人作證、疏散要人聽勸；外海還有一條船趕不趕得回來。
導演只放天氣與潮訊，不替誰做決定。

勝負很清楚：亥時（tick 72）前若「做海醮」或「焊水門」沒做成，潮頭灌進低處，
淹水格子會真的斷路；站在水裡的人會被捲走。誰活著、誰趕到、誰說動了誰，才是戲。

## 互斥的那一對（張鐵雄 ←→ 阿海／阿旺）

`t1`／`t2` 兩場真 LLM 實跑各是 11/11 目的達成、都 held，連結局文字都逐字相同——
因為原本六個人的目的**沒有一對是互斥的**，大家可以同時贏。達成率永遠 100%，
就分辨不出哪一組配裝比較好，而那正是配裝系統存在的理由。

所以給了一對真的咬住的：

    張鐵雄要在亥時前把水門焊死    ← 不焊，主峰灌進來，庄就沒了
    阿旺還在外海漁船上              ← 門一焊，他再也進不來
    阿海必須救回好兄弟阿旺          ← 門焊死前把人弄上岸，否則兩人都輸

阿海的 `prevent` 直接讀張鐵雄的第 0 個目的：**他成功，等於阿海失敗**，反之亦然。
和江湖的費彬／劉正風同一個形狀。兩邊都不是壞人——張鐵雄算過帳、認了那筆代價，
阿海也知道封門庄才保得住，只是好兄弟還在船上，他沒辦法先接受。
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
    "on_rite_done",
    "BRIEF", "NORMS", "PUBLIC_CAST", "PUBLIC_LORE", "SETTING", "EXAMPLES",
    "AGENTS", "DIRECTOR_SCRIPT", "TRACK_TOPICS", "NAME", "COMBAT",
    "STORM_TICK", "SAFE_AREAS", "LOW_AREAS", "SOUTH_AREAS", "OFFSHORE_AREAS", "RENAMED",
]

# ---------------------------------------------------------------- 地圖
# 北高南低：北三區避難，中間廣場與作坊，南貼海堤／碼頭，再往南是拉長的外海航道。
# 阿旺一早出海——航道約 45 格，全速（move_speed 3）回港大約 15 拍。
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
    "#~~~~~~~~~~~~~~~~~~~~~o~~~~#",
    "#~~~~~~~~~~~~~~~~~~~~~o~~~~#",
    "#~~~~~~~~~~~~~~~~~~~~~oooo~#",
    "#~~~~~~~~~~~~~~~~~~~~~~~~o~#",
    "#~~~~~~~~~~~~~~~~~~~~~~~~o~#",
    "#~~~~~~~~~~~~~~~~~~ooooooo~#",
    "#~~~~~~~~~~~~~~~~~~o~~~~~~~#",
    "#~~~~~~~~~~~~~~~~~~oooooo~~#",
    "#~~~~~~~~~~~~~~~~~~~~~~~o~~#",
    "#~~~~~~~~~~~~~~~~~ooooooo~~#",
    "#~~~~~~~~~~~~~~~~~o~~~~~~~~#",
    "#~~~~~~~~~~~~~~~~~oooooo~~~#",
    "#~~~~~~~~~~~~~~~~~~~~~~o~~~#",
    "#~~~~~~~~~~~~~~~~~oooooo~~~#",
    "#~~~~~~~~~~~~~~~~~oooo~~~~~#",
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
    "o": ("船甲板", True),
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
    Area("外海漁船", 15, 15, 26, 29,
         "港外遠處的漁船與回港航道。一早出海，全速駛回大約要一刻鐘上下（約十五拍）；"
         "水閘一焊死就回不了港，亥時一到海不留人。"),
]

# tick 72 = 第1天 18:00（與 jianghu 金盆時辰同一套鐘）
STORM_TICK = 72

# 結算用：禮成／閘封決定哪些低處會被淹。
SAFE_AREAS = ("高地", "鎮廟", "村長宅")  # 第一波到不了
SOUTH_AREAS = ("海堤", "漁港", "漁市")   # 閘沒封就先灌這裡
LOW_AREAS = ("海堤", "漁港", "漁市", "廣場", "鐵鋪", "糧倉")  # 兩樣都敗才全淹
OFFSHORE_AREAS = ("外海漁船",)  # 亥時外海必灌；人若還在船上，一定被捲走

BRIEF = f"""\
嵐潮鎮貼著海。北面是高地與鎮廟，南面是海堤和漁港，中間一條街穿過廣場。
鎮上不大，但從漁港跑到鎮廟要搶時間——暴潮來時，一刻鐘就決定生死。

今天不是普通的壞天氣。老輩人認得那種雲：潮會在今天傍晚前後上岸。
若亥時之前（約第 {STORM_TICK} 拍）鎮廟的做海醮沒做成、海堤水閘沒封上，
低處會真的被水吞掉——路斷、人捲走，那就是滅村。

哪裡做什麼，大家心裡都該有數：

- **鎮廟**：做海醮。至少兩名觀禮者從頭見到尾；起醮後要連續做完一段時間，空廟不算。
- **海堤**：焊水門。作證的人全程都得在場；一人焊約十二刻，再多一人上手可加快。
  門一焊死，外海回港的航道就斷了。
- **高地**：疏散的去處。帶不動人，醮與水門都救不了擠在低處的人。
- **漁港**：叫船回來。外海還有船的話，醮成了也會死人。
- **外海漁船**：阿旺一早出海，船在港外遠處，全速駛回漁港大約十五拍；水閘一焊死就進不來。
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
- 阿海，漁民，二十九歲。識潮；好兄弟阿旺還在外海船上，他得想盡辦法把人救回來。
- 阿旺，漁民，二十七歲。一早就出海捕魚，船還在外海遠處，回庄要一段航程。
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
- 水閘一焊死，外海回港的航道就斷——船上若還有人，等於判了海刑。
- 焊水門是苦力活：一個人焊要耗上一段時間；多一個人上手，能快一倍。作證的人全程都得看著。
- 做海醮也一樣，觀禮者要從頭見到尾，禮不是敲一下鐘就成。
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

情況：你是鐵匠，人已在海堤，旁邊有守望人，潮訊也確認了。
  thought: 有人看見就能開工。焊水門要一段時間，再拖，潮頭會先到。
  action: use_art / art=焊水門

情況：你是村長，廣場上有人發慌想往海堤看熱鬧。
  thought: 往南是送死。得把人趕上高地或鎮廟。
  action: use_art / art=派工 / target_agent=阿海 / utterance=你先去漁港接阿旺，收完上廟看醮。

情況：你是阿海，好兄弟阿旺還在外海船上，鐵雄往海堤去了。
  thought: 門一焊他就回不來。先攔閘搶時間，再衝回港接人。
  action: use_art / art=攔閘
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
封晚了，做海醮也擋不住灌進來的水。你跑得動，別浪費在廣場站著聽人哭。

焊水門是苦力活——一個人焊大約要十二刻才封得死；海堤上若再有人上手幫忙，能快一倍。
作證的人全程都得在你看得見的地方，走光了就得重來。
焊的時候你走不開；被人攔住，也得重來。門一焊死，外海回港的路就斷了。

你也知道門一焊，阿海的好兄弟阿旺要是還在外海船上，就再也進不來了。
你算過：等船，庄就沒了；不等，船上的人自己想辦法。這個帳你認，
但阿海不會認——他要是攔在閘前，你得自己決定怎麼辦。

還有一件事你比誰都清楚：**這門太早焊不得**。水門是全庄進出海的口子，
浪還沒起就封死，等於自己廢了漁港。要等潮訊確實、外海那道白線推近了才動得了手。
從那一刻到亥時，中間就那麼一段。太早焊不了，太晚焊不完。""",
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
             "params": {"who": ["shen_xi", "shi_lei", "a_qian", "a_wang",
                                "qing_he", "gu_chao"]}},
        ],
        "persona": """\
你叫林美華，嵐潮村長，三十八歲。庄裡人都叫你美華姐。
暴潮面前，村長的工作不是哭，是把人趕到該去的地方：鎮廟看醮、海堤作證、高地躲。

你今天要親自跟陳金水、張鐵雄對上話，確認禮與閘都有人做。
你可以用派工把人派出去，也可以安撫廣場上的恐慌——但你勸不動海，只能勸人。
阿海的兄弟阿旺還在外海船上：你得權衡「等船」與「封閘」，兩邊都會有人怪你。""",
    },
    {
        "id": "a_qian",
        "name": "阿海",
        "role": "villager",
        "home_area": "漁港",
        "start": (22, 11),  # 從糧倉出發，得自己跑回港
        "kin": ["a_wang"],
        "arts": ["ji_feng_bu", "wang_chao", "lan_zha"],
        "goals": [
            {"kind": "reach", "text": "先趕到漁港，對準外海那條船放訊號／接人",
             "params": {"area": "漁港"}},
            {"kind": "protect", "text": "想盡辦法讓好兄弟阿旺活過今天、別死在外海",
             "params": {"who": ["a_wang"]}},
            {"kind": "meet", "text": "親自把阿旺接上岸／當面對上他",
             "params": {"who": "a_wang"}},
            # 互斥點，見檔頭「互斥的那一對」。
            {"kind": "prevent", "text": "阿旺還沒上岸，別讓人把水門焊死",
             "params": {"agent": "shi_lei", "goal": 0}},
        ],
        "persona": """\
你叫阿海，漁民，二十九歲，識潮。全庄叫你阿海就好。

你有一個好兄弟叫阿旺——他一早就出海捕魚，船還在港外遠處，回港要一段航程。
暴潮要來了。鐵雄要把水門焊死；門一焊，阿旺那條船就回不來，人會被海吃掉。
你不是不知道封了門庄裡才保得住。你只是沒辦法在兄弟還在船上的時候先接受這件事。

今天你必須想盡辦法救他：跑回漁港放訊號、攔閘替他搶時間、找人幫你喊船、
必要時自己衝到碼頭接他上岸。他離岸還有一段路——你拖得住閘多久，可能就是他還能不能活。
救不回來，你這一天就全白了。

你手上有「攔閘」——真的擋得住鐵雄一陣子。但你也清楚兩件事：
擋得住的是一陣子，不是永遠；而且**擋過頭，潮頭壓過來，低處的人會跟著賠上**。
今天最難的不是攔不攔，是攔到什麼時候該讓開——而阿旺還在不在船上，會改變你的答案。""",
    },
    {
        "id": "a_wang",
        "name": "阿旺",
        "role": "villager",
        "home_area": "外海漁船",
        "start": (18, 29),  # 港外深處；全速回港約 15 拍
        "kin": ["a_qian"],
        "arts": ["wang_chao"],  # 船上沒有飛毛腿可搶——回港就是一段航程
        "goals": [
            {"kind": "reach", "text": "在水閘焊死、暴潮上岸之前，自己赶回漁港上岸",
             "params": {"area": "漁港"}},
            {"kind": "survive", "text": "活過今天，別被暴潮捲走"},
            {"kind": "meet", "text": "上岸後找到好兄弟阿海",
             "params": {"who": "a_qian"}},
        ],
        "persona": """\
你叫阿旺，漁民，二十七歲。庄裡人把你和阿海當一對——出海、喝酒、吵架都是兩個人。

你一早就駕船出海捕魚，此刻人還在港外遠處的漁船上。潮色不對了。
回港不是上岸跑步——你得沿著航道把船往漁港駛，這段海路全速也大約要十五拍。
門一焊死，航道就斷，你進不去；亥時一到，外海一樣會吞人。

你能看潮。別在船上發呆——駕船往漁港趕，靠岸後找阿海。他一定在為你拼命，
你也得自己把命駛回來。""",
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

你能探潮打探別人在哪，也能飛毛腿趕路。別在廣場聽人哭，海堤才是你的崗。
外海若還看得到阿旺那條船的燈，跟阿海說一聲——那可能比多看一眼雲色還重要。""",
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
        "text": "（外海一道白線平行岸邊推近。碼頭木頭咯吱響——"
                "港外遠處還看得到一盞船燈，那船離岸還有一段航程。）",
    },
    {
        "tick": 48,
        "kind": "broadcast",
        "area": "廣場",
        "text": "（風把招牌掀下來。有人往南跑去看潮，有人往北擠。廣場亂了。"
                "有人喊：阿旺那條船還沒回來！）",
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

TRACK_TOPICS = ["暴潮", "海醮", "焊水門", "海堤", "高地", "漁港", "外海", "阿旺",
                "疏散", "看醮", "滅村"]


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
            "外海": "外海漁船",
            "漁船": "外海漁船",
            "船": "外海漁船",
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
            kin=list(spec.get("kin", [])),
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


def _seal_offshore_channel(engine) -> None:
    """水門焊死後，外海回港航道斷開——還在船上的人進不了港。

    只淹外海甲板／航道，不淹漁港本體；亥時暴潮結算仍會處理低處與外海溺斃。
    """
    grid = engine.grid
    cells = grid.cells_in_areas(list(OFFSHORE_AREAS))
    if not cells:
        return
    n = grid.flood_positions(cells)
    if n <= 0:
        return
    w, t = engine.world, engine.world.tick
    when = clock_str(t)
    text = (
        "（水門焊死了。外海回港的航道被閘口截斷——還在船上的人，"
        "這會兒只能看著岸燈，進不來了。）"
    )
    engine.log.write(
        "director",
        {"kind": "broadcast", "area": None, "text": text, "fired": True, "tag": "gate"},
    )
    w.flooded = [p.as_list() for p in sorted(grid.flooded, key=lambda q: (q.y, q.x))]
    for a in w.agents.values():
        if a.alive:
            a.memory.add(t, when, "observation", text, importance=10)
        if engine.director is not None:
            engine.director.add_runtime(a.id, text, t + 1, tag="gate")
    if engine.console:
        engine.console.print(f"[bold cyan]≈ {text}[/bold cyan]")


def on_rite_done(engine, agent, rite: str) -> None:
    """長儀式完工掛鉤：焊水門一成，立刻切斷外海回港路。"""
    if rite == "焊水門":
        _seal_offshore_channel(engine)


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

    # 外海甲板亥時必灌：人若還在船上，不論閘封不封都救不回來。
    flood_names = list(flood_names) + list(OFFSHORE_AREAS)

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
