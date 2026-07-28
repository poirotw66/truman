# truman — LLM 多智能體箱庭

一座格子地圖、幾個語言模型代理。**沒有預寫劇本結局**——人設目標互相排斥，張力自己會長出來。

## 目標主線

專案已從「單主角楚門式覺察實驗」轉向：

> **箱庭：把目標互相衝突的 AI 放進世界，看社會互動（與必要時的動手）會不會自己長出來。**

目前最尖的案子是 **`scenarios/jianghu.py`（武林箱庭）**：笑傲江湖・劉正風金盆洗手那一天。知識對稱、世界允許 `attack`、死亡不可逆；導演不推人，只放世界本身會發生的事。

要問的不是「誰先發現世界是假的」，而是：

- 結構性衝突下，對話／站位／動手是否可信？
- 「強者通吃」能不能被機制打破？（anti-snowball：傷勢非對稱、義憤、親友尋仇）
- 角色能不能感受到自己身上的狀態？（傷勢、憤怒——機制寫了卻進不了 prompt 就不算數）

和平對照本是 `hakoniwa`（同一張海晏鎮地圖、無動手）。`seahaven` 仍保留為早期楚門劇本，有主角時才啟用覺察評審；箱庭劇本沒有主角，該層整層不存在。

---

## 快速開始

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[all]"   # 或 .[anthropic] / .[gemini]
copy .env.example .env      # 填你要用的那一家的 key
```

支援兩家 provider，用全域旗標切換（預設 `anthropic`）：

```powershell
.\.venv\Scripts\python.exe -m truman.cli --provider gemini --scenario jianghu run `
  --run-id j2 --ticks 96 --seed 7
```

```powershell
# 不花錢，先確認一切正常
.\.venv\Scripts\python.exe -m tests.smoke
.\.venv\Scripts\python.exe -m truman.cli --scenario jianghu run --run-id dry --ticks 30 --stub
.\.venv\Scripts\python.exe -m truman.cli report --run-id dry

# Demo 前端：回放既有 run，或現場開一場（預設 stub；真內容請關 stub）
.\.venv\Scripts\python.exe -m truman.demo                        # http://127.0.0.1:8765

# 看地圖、快取前綴、對帳模型 ID
.\.venv\Scripts\python.exe -m truman.cli --scenario jianghu map
.\.venv\Scripts\python.exe -m truman.cli tokens
.\.venv\Scripts\python.exe -m truman.cli models

# 出報告：節流率、動手／死亡、對話圖、話題擴散、reflection、成本
.\.venv\Scripts\python.exe -m truman.cli report --run-id j2

# 零成本重放（讀日誌，不呼叫 API）
.\.venv\Scripts\python.exe -m truman.cli --scenario jianghu replay --run-id j2

# 事件日誌 → 完整回放頁（可接力多段 fork）
.\.venv\Scripts\python.exe replay\build_frames.py --run j2 --run j2b --run j2c --out j2_replay.html

# 反事實分支：從最新 checkpoint 岔出去
.\.venv\Scripts\python.exe -m truman.cli --scenario jianghu fork `
  --from-latest j2 --run-id j2_x --ticks 24 `
  --inject "liu_zhengfeng:（廳外忽然傳來一陣急驟的馬蹄聲。）"
```

長跑請用 `nohup … & disown` 脫離互動 session，否則 event loop 可能被掛住。

---

## 劇本

| 劇本 | 知識 | 動手 | 要看什麼 |
|---|---|---|---|
| **jianghu**（現役主線） | 對稱 | 是 | 金盆洗手火藥桶；社交＋殺戮動力學、anti-snowball |
| **hakoniwa** | 對稱 | 否 | 和平小鎮；小聚引信會不會傳開 |
| **seahaven**（早期） | 不對稱（一主角五演員） | 否 | 主角多久開始懷疑——已非主線，管線仍支援 |

江湖六人目標互相排斥（摘自劇本）：

```
劉正風要洗手金盆、退出江湖        ← 費彬奉命阻止，不惜見血
曲洋是日月神教長老                ← 在這裡被認出來就是死罪
田伯光看上了儀琳                  ← 令狐沖不會坐視
儀琳只想活著回恆山
```

沒有人是單純的好人或壞人；衝突是結構性的。這正是箱庭要的。

### Anti-snowball（已在 j2 全日驗證）

為了對抗「強者通吃」，戰鬥側有三條機制（細節見 `todo.md` 與 `Engine._resolve_attack`）：

1. **玻璃刀**：傷勢大幅拖垮守勢（帶傷反擊仍可能贏，但再擋不住）
2. **背水一戰**：帶傷出手有攻勢加成（目前幾乎不被 LLM 觸發——見下方待辦）
3. **義憤 `fury`**：見殺累積，進骰子（目前未進 prompt——角色不知道自己在氣）

j2→j2b→j2c 全日重跑裡，費彬第一個死於曲洋（與 j1「費彬殺四人、唯一生還」相反）；傍晚安靜、死者較分散。串回放：

```powershell
.\.venv\Scripts\python.exe replay\build_frames.py --run j2 --run j2b --run j2c --out j2_replay.html
```

---

## 架構

```
Director ── 只改「誰能觀察到什麼」，不改世界狀態
   │         （箱庭裡多半是世界廣播，不是給演員下指令）
   ▼
World Engine ── 權威狀態。agent 只提交 intent，這裡驗證後才生效
   │  Observation（每 agent 的過濾投影）
   ▼
Agent Cognition ── perceive → retrieve → plan → act → reflect
   │
   ▼
Event Log（JSONL）+ Checkpoint ── 可 replay、可分支
```

四個不能妥協的設計決定：

1. **agent 不能直接改世界狀態。** 只能提交 intent，`Engine._apply_intent` 驗證。
   驗證失敗會把錯誤寫回它的記憶，否則它會一直重複同一個幻覺。
2. **Observation 是世界狀態的過濾投影。** 導演的所有操縱都掛在這一層。
   世界引擎仍是唯一權威，所以分支重跑一切可重現。
3. **離散 tick + action queue。** lockstep 好 debug、好 replay，對 prompt cache 友善。
4. **全量日誌從第一天就有。** 沒有它就不能 replay；不能 replay 等於每次分析都要重燒一次錢。

### 遺留：seahaven 的不對稱與覺察

僅在有 `protagonist` 的劇本生效。主角人設不提節目、演員人設明寫攝影棚；覺察偵測是外部事後評審（樣式哨兵 + 每 24 tick LLM judge），**不**寫進 action schema。箱庭劇本呼叫不到這層。

---

## Provider

模擬本身完全不知道背後是哪一家 —— 它只看得到 `Call` 和 `run_batch()`。
差異壓在 `BaseLLMClient._invoke()` 這一個方法裡（`truman/llm/providers/`）。

|  | Anthropic | Gemini |
|---|---|---|
| API | Messages | Interactions（`store=False`，無狀態） |
| 快取 | **顯式斷點**：system 切兩塊，各打一個 `cache_control` | **隱式**：`system_instruction` 只能是單一字串，由服務端自己找最長共同前綴 |
| 快取寫入成本 | 1.25×（5m TTL）／ 2×（1h） | 無（Interactions API 不支援 explicit cache，也就沒有儲存費） |
| 快取讀取 | 輸入價 × 0.1 | 各模型獨立的 cached 價（約輸入價的 1/10） |
| 結構化輸出 | `output_config.format` | `response_format={"text": {"mimeType", "schema"}}` |
| 推理深度 | `thinking` + `output_config.effort` | `generation_config.thinking_level` |
| 用量欄位 | `usage.input_tokens` **不含**快取 | `usage.total_input_tokens` **含**快取，要自行扣除 |

兩邊共通：**前綴低於門檻就靜默失效**，不報錯、不多收錢、帳單上看不出來。所以 `report` 一定要看「快取讀」欄位。

`Call.system_blocks` 的順序在兩邊都一樣重要（世界在前、人設在後）。

> ⚠️ **模型 ID 與價格查證於 2026-07-23。** 這些會變。跑之前用 `truman.cli models` 對一次帳。
> `config.py` 裡標 `*` 的快取門檻是依世代推定的。

## 成本

三個機制在 `truman/llm/base.py`（provider 共用）：

**1. 節流閥（最大槓桿）** — `cognition.needs_llm`。多數 tick 沒事發生就不叫 LLM。
只有動作做完、被搭話、聽見對話、看到新面孔、導演事件，或每 6 tick 保底才思考。

**2. 循序暖機（只在划算時做）** — 前綴真的進得了快取才先送一個再並行；否則整批並行
（日誌寫 `warmup_skipped`）。依模型分組判斷。

**3. 分層路由** — routine / dialogue / reflect / judge。有主角時 `protagonist_min_tier` 保底。

### 快取門檻（實測摘要）

前綴約 **2200 tokens**。2048 門檻跨得過、4096 跨不過 → Gemini 高流量層宜用能命中快取的型號；
jianghu 現行 3.x 前綴往往進不了 4096，暖機會被跳過（這是對的）。

### 粗估

6 個 agent、96 tick、節流率約 50%（約 290 次呼叫）：

| 項目 | Anthropic | Gemini |
|---|---:|---:|
| routine | ~$0.70 | ~$0.03 |
| dialogue | ~$0.70 | ~$0.10 |
| reflection | ~$1.05 | ~$0.36 |
| 覺察評審（僅有主角時） | ~$0.04 | ~$0.01 |
| **合計 / 模擬日** | **≈ $2.5** | **≈ $0.5** |

reflection 佔大頭。要砍成本先動 `reflection_threshold`。實跑請以 `report` 成本表為準。

---

## 目前的取捨與升級路徑

| 現在 | 為什麼 | 升級路徑 |
|---|---|---|
| 記憶檢索用詞彙重疊（中文 bigram） | 不想在 Phase 1 引入 torch / sentence-transformers | 換掉 `MemoryStream.relevance()` 即可 |
| importance 用規則給分 | 用 LLM 評分會讓每 tick 呼叫數翻倍 | `cognition.IMPORTANCE` 換成批次評分 |
| 對話是「同 tick 追加一輪」 | 讓交談自然，成本又有上限 | 放寬 `_burst_targets` 輪數 |
| 視野不被牆擋 | 地圖小，LOS 不划算 | `build_observations` 加 Bresenham |
| 沒有物件系統 | `interact` 自由文字夠用 | `WorldState` 加 `objects` |

## 已知問題／主線待辦

詳見 [`todo.md`](todo.md)。最貼主線的兩條：

- **背水一戰幾乎不觸發**：帶傷者很少出手；機制要進 observation／prompt，否則白寫。
- **義憤累積了卻沒轉成行動**：`fury` 只進骰子、不進 prompt；角色不知道自己在憤怒。

其餘：

- **共識塌縮**：跑久了可能互相附和。箱庭靠對立人設與場所競爭撐張力。
- **記憶膨脹**：`memory_cap=400`；reflection 永不丟。
- **replay 一致性**：依賴 `(tick, agent, 用途)` key；改節流或人員後舊日誌會對不上。
- **尋仇的 fork 韌性**：runtime inject 不進 WorldState；checkpoint 卡在死亡與觸發之間可能漏報。

## 檔案地圖

```
truman/
  config.py              時間制度、模型路由、成本推導、節流參數
  world/grid.py          格子地圖、區域、BFS 尋路
  world/state.py         WorldState / AgentState（完全可序列化）
  world/observation.py   每 agent 的過濾投影 ← 導演的掛載點
  world/engine.py        tick 迴圈、intent 驗證、對話追加輪、戰鬥
  agents/memory.py       memory stream + 三要素檢索
  agents/cognition.py    節流閥、模型選層、prompt 組裝、記憶寫入
  director/director.py   inject / broadcast / summon / cue
  director/awareness.py  覺察評審（僅有主角時）
  llm/…                  provider 共用與 Anthropic / Gemini 實作
  obs/eventlog.py        JSONL 全量日誌
  obs/checkpoint.py      存檔 / 讀檔 / 分支
  cast.py                人物設定檔（--cast）
  demo/                  本機 demo 入口（回放 + 現場開跑）
scenarios/
  jianghu.py             武林箱庭（現役主線）
  hakoniwa.py            和平箱庭
  seahaven.py            早期楚門劇本
tests/smoke.py           離線煙霧測試
web/pixelart.js          共用像素美術
art/…                    立繪產生與內嵌
cast/build_editor.py     人物工作室
replay/build_frames.py   事件日誌 → 回放 HTML
todo.md                  主線待辦與實跑紀錄
```

## 人物立繪

```powershell
.\.venv\Scripts\python.exe art\gen_portraits.py            # 已存在的跳過
.\.venv\Scripts\python.exe art\gen_portraits.py --force    # 全部重畫
```

一致性靠：**共用同一段 `STYLE`**（一字不改）＋ 風格錨當參考影像。畫完重跑
`replay/build_frames.py` 與 `cast/build_editor.py`。沒立繪時退回像素立繪。

## 開場之前：人物工作室

```powershell
.\.venv\Scripts\python.exe cast\build_editor.py jianghu   # 產出 cast_editor.html
```

改名字／人設／武功／起始位置／在意的人／長相，下載 `cast/jianghu.json` 後：

```powershell
.\.venv\Scripts\python.exe -m truman.cli --scenario jianghu run `
  --run-id j2 --ticks 96 --seed 7 --cast cast\jianghu.json
```

### 每個人可以掛不同的腦袋

```json
{"id": "fei_bin", "llm": {"model": "gemini-3.5-flash", "temperature": 0.15}}
```

不設就走分層路由。這是最乾淨的對照：**同劇本、同 seed，只換一個人的腦袋**。
報表會把自帶模型的呼叫另外分桶計價（`routine·gemini-3.5-flash`）。

劇本 `.py` 一個字都不會動——設定檔只在那一次 run 生效。`cast/<劇本>.default.json`
是原設定，方便 diff。
