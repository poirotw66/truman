"""絕技目錄——這個世界裡的「工具」。

一門絕技就是一個 tool：它有名字、有說明、有使用時機、有前提條件、有配額。
角色（LLM）自己決定什麼時候用；用不用得成由世界判定，不是由它宣告。
這和 `attack` 是同一條原則：agent 只提交 intent。

拆成兩半：

    ArtDef   靜態定義——叫什麼、做什麼、什麼時候該用。放在這裡，用 id 查。
    Art      動態狀態——還剩幾次、冷卻到哪一拍。放在 AgentState，進 checkpoint。

這樣改說明文字不會讓舊存檔失效，checkpoint 也不必把同一段文字抄六份。

## 為什麼絕技要有配額和冷卻

不是為了平衡，是為了讓「選擇」有重量。一門能用無限次的絕技，
LLM 會每個 tick 都拿出來用，那就退化成被動加成，看不出它在權衡什麼。
限次數的工具會逼出「現在值不值得用掉」——那才是我們想看的決策。

## 效果一覽（引擎怎麼解，見 Engine._resolve_art）

    atk_up    自己接下來幾拍攻勢加成
    def_up    自己接下來幾拍守勢加成
    dash      自己接下來幾拍腳程加倍
    soothe    範圍內所有人（含自己）義憤下降
    denounce  當眾指證某人某事：在場所有人記下這件事，被指的人身分曝光
    veil      自己接下來幾拍不會被 denounce 指實（擋掉一次）
    lure      對身邊某人說一段有說服力的話，在他記憶裡種下強烈的動機
    scout     打聽某人現在在哪個區域，即使看不見他
    rite      當眾完成一場儀式（需要有人在場觀禮）
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 需要指定對象的類型
TARGET_NONE = ""
TARGET_AGENT = "agent"
TARGET_AREA = "area"

KINDS = ("combat", "social", "info", "move")


@dataclass(frozen=True)
class ArtDef:
    """一門絕技的靜態定義。`when` 就是給 LLM 看的 tool description。"""

    id: str
    name: str
    kind: str
    tagline: str  # 一句話說明這招是什麼（給人看的）
    when: str  # 什麼時候該用（給 LLM 看的判斷依據）
    effect: str
    params: dict = field(default_factory=dict)
    uses: int = -1  # -1 = 不限次數
    cooldown: int = 0  # 用完之後幾拍不能再用
    target: str = TARGET_NONE
    reach: int = 0  # 需要多近才用得出來（0 = 不必靠近／不需對象）
    combat_only: bool = False  # 和平劇本不掛這門

    def cost_line(self) -> str:
        """配額寫成一句話。無限次又無冷卻就不必提。"""
        bits = []
        if self.uses >= 0:
            bits.append(f"今天只能用 {self.uses} 次")
        if self.cooldown:
            bits.append(f"用過要隔 {self.cooldown} 刻才能再用")
        return "，".join(bits)


# ------------------------------------------------------------------ 目錄
#
# 每一門都綁著某個角色的目的。「配了絕技卻對自己的目的沒幫助」是設計失敗——
# 六個人裡只有兩個的目的靠打架達成，所以社交／情報／身法的比重刻意高於戰鬥。

CATALOG: dict[str, ArtDef] = {
    # ---------------------------------------------------------- 戰鬥
    "da_song_yang": ArtDef(
        id="da_song_yang",
        name="大嵩陽手",
        kind="combat",
        tagline="嵩山派的剛猛掌法，一掌下去對方多半接不住。",
        when="你已經決定要動手，而且想一擊見效的時候。先運起這門功夫，"
        "接下來幾刻你出手都會重得多。單用它不會傷到人，你還是得真的出手。"
        "對手武功不弱、或你想速戰速決時，先使這門再 attack。",
        effect="atk_up",
        params={"amount": 3, "ticks": 3},
        uses=3,
        cooldown=6,
        combat_only=True,
    ),
    "kuai_dao": ArtDef(
        id="kuai_dao",
        name="快刀",
        kind="combat",
        tagline="出刀比別人快半拍，江湖上十幾年沒人接得住。",
        when="要動手，而且對方武功不弱的時候。快的那半拍就是勝負。"
        "單用它不會傷到人，你還是得真的出手。先使這門再 attack。",
        effect="atk_up",
        params={"amount": 3, "ticks": 3},
        uses=3,
        cooldown=6,
        combat_only=True,
    ),
    "heng_shan_jian": ArtDef(
        id="heng_shan_jian",
        name="衡山劍法",
        kind="combat",
        tagline="以守為主的劍路，守得住就有轉圜。",
        when="你不想動手、但對方可能先動手的時候。它保不了你不受傷，"
        "只是讓你比較擋得住。要拖時間、要把場面壓下來，這門比拔刀有用。",
        effect="def_up",
        params={"amount": 3, "ticks": 4},
        uses=3,
        cooldown=5,
        combat_only=True,
    ),
    "hua_shan_jian": ArtDef(
        id="hua_shan_jian",
        name="華山劍法",
        kind="combat",
        tagline="正宗的劍路，攻守都還過得去。",
        when="要擋在別人前面的時候。你打不過這裡多數人，但擋一陣子還行。",
        effect="def_up",
        params={"amount": 3, "ticks": 4},
        uses=3,
        cooldown=5,
        combat_only=True,
    ),
    "jiu_dan": ArtDef(
        id="jiu_dan",
        name="酒膽",
        kind="combat",
        tagline="灌一口酒，痛就不那麼痛了，手也不抖了。",
        when="你已經帶著傷、卻還非出手不可的時候。"
        "這門功夫只在你身上有傷時才有用，沒傷喝了只是浪費酒。",
        effect="atk_up",
        params={"amount": 4, "ticks": 3, "require_wound": True},
        uses=2,
        cooldown=4,
        combat_only=True,
    ),
    # ---------------------------------------------------------- 社交
    "ming_zheng_yan_shun": ArtDef(
        id="ming_zheng_yan_shun",
        name="名正言順",
        kind="social",
        tagline="當著滿場的人指證某人一樁大罪，讓他再也說不清楚。",
        when="你手上有對方的把柄，而且旁邊有夠多人聽得見的時候。"
        "這一招不流血，但比流血更難收拾——說出去就收不回來了，"
        "所以時機和在場的人是誰，比說什麼更要緊。",
        effect="denounce",
        params={"claim": "私通魔教"},
        uses=1,
        target=TARGET_AGENT,
        reach=3,
    ),
    "guang_ling_san": ArtDef(
        id="guang_ling_san",
        name="廣陵散",
        kind="social",
        tagline="一曲琴音，聽見的人心裡那股火會慢慢降下來。",
        when="場面繃得太緊、有人快動手的時候。琴音壓得住殺氣，"
        "但也會把懂琴的人引過來——那可能正是你要的，也可能會害了你。",
        effect="soothe",
        params={"amount": 2, "radius": 5},
        uses=2,
        cooldown=8,
    ),
    "nian_jing": ArtDef(
        id="nian_jing",
        name="誦經",
        kind="social",
        tagline="念一段經，自己定下來，旁邊的人也會靜一點。",
        when="你怕得發抖、或者眼前剛出過人命的時候。"
        "它救不了你的命，但能讓你不要在最不該慌的時候慌掉。",
        effect="soothe",
        params={"amount": 2, "radius": 3},
        uses=-1,
        cooldown=6,
    ),
    "hua_yan_qiao_yu": ArtDef(
        id="hua_yan_qiao_yu",
        name="花言巧語",
        kind="social",
        tagline="幾句話就能讓人相信跟你走是自己的主意。",
        when="你要把某個人帶到別的地方去，而硬來會鬧大的時候。"
        "對方是不是真的信、要不要跟你走，仍然是他自己決定——"
        "你只是給了他一個聽起來很有道理的理由。",
        effect="lure",
        params={"weight": 8},
        uses=3,
        cooldown=4,
        target=TARGET_AGENT,
        reach=3,
    ),
    "jin_pen_xi_shou": ArtDef(
        id="jin_pen_xi_shou",
        name="金盆洗手",
        kind="social",
        tagline="當眾把手洗進金盆，從此退出江湖。江湖上最重的一個禮。",
        when="吉時到了、賓客都在場的時候。這是你今天唯一真正要做的事。"
        "洗完就成了，收不回來；沒人在場看著就不算數，得有人觀禮。",
        effect="rite",
        params={"area": "劉府", "witnesses": 1, "rite": "金盆洗手"},
        uses=1,
    ),
    # ---------------------------------------------------------- 身法
    "wan_li_du_xing": ArtDef(
        id="wan_li_du_xing",
        name="萬里獨行",
        kind="move",
        tagline="輕功極好，來去無蹤，十幾年沒人追得上。",
        when="要甩開後面的人，或者要在別人反應過來之前趕到某個地方。",
        effect="dash",
        params={"multiplier": 2, "ticks": 3},
        uses=3,
        cooldown=5,
    ),
    "heng_shan_qing_gong": ArtDef(
        id="heng_shan_qing_gong",
        name="恆山輕功",
        kind="move",
        tagline="恆山派的腳程，跑不快但撐得久。",
        when="你打不過又不想死的時候。逃不是丟臉的事，你本來就打不過任何人。",
        effect="dash",
        params={"multiplier": 2, "ticks": 3},
        uses=3,
        cooldown=5,
    ),
    "yin_ni_xing_cang": ArtDef(
        id="yin_ni_xing_cang",
        name="隱匿行藏",
        kind="move",
        tagline="收起所有會露底的東西，讓人一時認不出你是誰。",
        when="你覺得有人在查你、或者有人快要當眾說破你的來歷的時候。"
        "它只擋得住一次，而且撐不了多久。",
        effect="veil",
        params={"ticks": 6},
        uses=2,
        cooldown=10,
    ),
    # ---------------------------------------------------------- 情報
    "da_ting": ArtDef(
        id="da_ting",
        name="打聽",
        kind="info",
        tagline="找街上的人問幾句，就知道某個人現在在哪。",
        when="你要找的人不在眼前，而你不想瞎走的時候。"
        "問到的是他此刻在哪個地方，不是他在做什麼。"
        "一定要在 target_agent 填他的名字；名字空著一定使不出來。",
        effect="scout",
        params={},
        uses=4,
        cooldown=2,
        target=TARGET_AGENT,
    ),
    "jiang_hu_er_mu": ArtDef(
        id="jiang_hu_er_mu",
        name="江湖耳目",
        kind="info",
        tagline="道上有人替你看著，誰進了城、誰往哪去，你比別人早知道。",
        when="你想避開某個人，或者想知道某個人有沒有跟上來的時候。"
        "一定要在 target_agent 填他的名字；名字空著一定使不出來。",
        effect="scout",
        params={},
        uses=4,
        cooldown=2,
        target=TARGET_AGENT,
    ),
    # ---------------------------------------------------------- 嵐潮（沿海救村）
    "zhen_chao_li": ArtDef(
        id="zhen_chao_li",
        name="鎮潮禮",
        kind="social",
        tagline="在鎮廟當眾行禮，請海神把暴潮壓下去。全鎮的命懸在這一場。",
        when="吉時將近、鎮廟裡有人觀禮的時候。這是今天唯一能擋滅村的大禮。"
        "沒人看見就不算；潮頭一過再做也晚了。",
        effect="rite",
        params={"area": "鎮廟", "witnesses": 2, "rite": "鎮潮禮"},
        uses=1,
    ),
    "feng_zha": ArtDef(
        id="feng_zha",
        name="封閘",
        kind="social",
        tagline="把海堤水閘焊死、頂死，讓第一波潮水衝不進鎮裡。",
        when="你人在海堤、旁邊還有人能作證的時候。閘封晚了，禮也救不了低處。"
        "這是苦力活，不是空話。",
        effect="rite",
        params={"area": "海堤", "witnesses": 1, "rite": "封閘"},
        uses=1,
    ),
    "wang_chao": ArtDef(
        id="wang_chao",
        name="望潮",
        kind="info",
        tagline="憑潮聲、雲色和鳥飛，判斷誰還在哪裡、潮還有多遠。",
        when="你要找的人不在眼前，而暴潮不等人的時候。"
        "問到的是他此刻在哪個地方。一定要填對象名字。",
        effect="scout",
        params={},
        uses=5,
        cooldown=1,
        target=TARGET_AGENT,
    ),
    "ji_feng_bu": ArtDef(
        id="ji_feng_bu",
        name="疾風步",
        kind="move",
        tagline="頂著風跑，別人寸步難行時你還能趕路。",
        when="要在潮來之前趕到海堤、鎮廟或高地的時候。慢一步，整條街就沒了。",
        effect="dash",
        params={"multiplier": 2, "ticks": 4},
        uses=4,
        cooldown=3,
    ),
    "an_min_zhou": ArtDef(
        id="an_min_zhou",
        name="安民咒",
        kind="social",
        tagline="幾句穩得住人心的話，讓旁邊慌亂的人先定下來。",
        when="廣場或高地上有人快要崩潰、互相推擠的時候。"
        "它擋不住潮水，但能讓疏散還有秩序。",
        effect="soothe",
        params={"amount": 2, "radius": 4},
        uses=3,
        cooldown=5,
    ),
    "hao_ling": ArtDef(
        id="hao_ling",
        name="號令",
        kind="social",
        tagline="用村長的口吻把人往該去的地方趕——聽不聽仍是他自己的事。",
        when="你要把某人勸去鎮廟觀禮、去海堤幫忙、或撤上高地的時候。",
        effect="lure",
        params={"weight": 9},
        uses=4,
        cooldown=3,
        target=TARGET_AGENT,
        reach=3,
    ),
}


def get(art_id: str) -> ArtDef | None:
    return CATALOG.get(art_id)


def unknown(art_ids) -> list[str]:
    """回傳目錄裡沒有的 id。設定檔驗證用——錯字要在開跑前抓到。"""
    return [a for a in art_ids if a not in CATALOG]


def equip(art_ids) -> list:
    """把 id 清單變成可以掛在 AgentState 上的 Art（帶著各自的初始配額）。"""
    from .state import Art  # 避免循環匯入

    out = []
    for aid in art_ids:
        d = CATALOG.get(aid)
        if d is None:
            continue
        out.append(Art(id=aid, uses_left=d.uses, ready_at=-1))
    return out


def resolve_name(text: str) -> str | None:
    """LLM 填的是招式名字（「大嵩陽手」），這裡換回 id。也接受直接填 id。"""
    t = (text or "").strip()
    if not t:
        return None
    if t in CATALOG:
        return t
    for d in CATALOG.values():
        if d.name == t:
            return d.id
    # 容錯：模型偶爾會把招式名寫進括號或加上「用」字
    for d in CATALOG.values():
        if d.name and d.name in t:
            return d.id
    return None
