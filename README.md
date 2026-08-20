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
- **給了明確的目的與趁手的工具，agent 會不會用？用得對不對？**

和平對照本是 `hakoniwa`（同一張海晏鎮地圖、無動手）。`seahaven` 仍保留為早期楚門劇本，有主角時才啟用覺察評審；箱庭劇本沒有主角，該層整層不存在。

### 目的與絕技：每個人都知道自己要什麼，也帶著能達成它的東西

每個角色身上有兩樣結構化、世界看得懂的東西：

| | 是什麼 | 在哪 |
|---|---|---|
| **目的（goal）** | 今天要做到的事，寫成世界判定得出來的形式 | `truman/world/goals.py` |
| **絕技（art）** | 他手上的工具：有說明、有使用時機、有前提、有配額 | `truman/world/arts.py` |

**絕技就是這個世界的 tool。** 它有名字、有「什麼時候該用」的說明、有前提條件、有次數與冷卻；
角色自己決定何時拿出來，用不用得成由世界判定——和 `attack` 同一條原則：agent 只提交 intent。
這個對照是刻意的，它把 agent 的運作攤開來給人看：

```
tool definition   ArtDef（名字／說明／使用時機／配額）  → system[1]，整場不變
tool state        Art（還剩幾次／冷卻到哪一拍）        → observation，每拍都變
tool call         action.kind = "use_art"
tool runtime      Engine._apply_art → _resolve_art     → 驗參數、執行、把結果回給他
```

六個人的目的互相咬合，這是整個劇本的重點：

```
劉正風要洗完手      ←→  費彬的目的就是「讓他洗不成」（prevent 直接讀劉的目的）
劉正風怕事情被說破  ←→  費彬的「名正言順」正好是說破用的
曲洋怕被認出來      ←→  同一門「名正言順」也可以指向他
田伯光要把儀琳帶走  ←→  儀琳要活著走到城門，令狐沖要她不死
```

**費彬的「名正言順」一天只能用一次**，可以指劉正風、也可以指曲洋——兩個都是他要的，
只能挑一個。那一次選擇是這場箱庭最有看頭的地方，也是「工具有配額」最好的示範。

絕技刻意不只有戰鬥（目前四類十五門：戰鬥／社交／情報／身法）。六個人裡只有兩個的目的
靠打架達成；只給刀劍的話，儀琳、劉正風、曲洋會拿著一身用不上的武功，去追一個打不出來的目的。

判定一律是**純函式、不呼叫 LLM**。理由和戰鬥把隨機源綁死在 `(seed, tick, 攻, 守)` 上一樣：
replay 必須重現同一個結局，否則「達成率」這個數字沒有意義。八個判定器：
`survive` / `reach` / `ritual` / `protect` / `prevent` / `isolate` / `conceal` / `meet`。

沒配目的、沒配絕技的角色，這兩層**整層不存在**——`use_art` 連 schema 的 enum 都不會出現。
和平劇本讀起來和以前一模一樣。

### 最乾淨的對照實驗：同劇本、同 seed，只改配裝

```powershell
# 1. 開工作室，改目的、換絕技（點卡片就是配上／拿掉）
.\.venv\Scripts\python.exe cast\build_editor.py jianghu

# 2. 拿下載的 cast.json 開跑
.\.venv\Scripts\python.exe -m truman.cli --scenario jianghu run `
  --run-id j5 --ticks 96 --seed 7 --cast cast\jianghu.json

# 3. 看達成率與絕技使用統計
.\.venv\Scripts\python.exe -m truman.cli report --run-id j5
```

把費彬的「名正言順」拿掉，他還攔得住嗎？給儀琳多一門輕功，她走得到城門嗎？
這類問題現在有數字可以回答，不必靠讀對話猜。

### 目前狀態（本輪收尾）

這一輪停在「可以 demo、可以重現、主線問題寫清楚」：

| 已到位 | 說明 |
|---|---|
| **anti-snowball 全日驗證** | `j2`→`j2b`→`j2c`：費彬先死於曲洋，「玻璃刀」守勢懲罰確實打破強者通吃 |
| **目的與絕技** | 六人各有可判定的目的與配備的絕技；工作室可配裝、report 出達成率、回放頁有任務卡與成績單 |
| **傷勢／義憤進 prompt** | 兩條 anti-snowball 機制原本只進骰子，角色感受不到；現在寫進 observation |
| **本機 demo 入口** | `python -m truman.demo`：回放既有 run，或現場開跑（SSE 進度含時辰／phase／已花時間／平均每 tick／預估剩餘） |
| **接力回放** | `replay/build_frames.py` 可串多段 fork；checkpoint 差一拍已修 |
| **預設 provider** | `gemini` · 全層 `gemini-3.1-flash-lite`（格式較穩；快取門檻 4096，常見前綴常進不去） |

**上台建議先播回放，不要賭現場燒一整天。** 真 LLM 全日（96 ticks）本來就慢，前端只能誠實顯示進度，無法變快。推薦串：`j2` + `j2b` + `j2c`。

**目的與絕技已由嵐潮 `t1`／`t2` 實跑驗證**（各 96 拍真 LLM、約 $1.2、零呼叫失敗）：
六門絕技全被用過（`when` 說明寫得夠清楚）、`use_art` 幾乎不被駁回（角色讀得到剩餘次數）。

⚠ 但兩場**跑出完全相同的結果**（11/11 目的達成、都 held、連結局文字都一樣），
因為當時嵐潮六個人的目的沒有一對是互斥的。達成率永遠 100%，就分辨不出哪一組配裝比較好——
而那正是配裝系統存在的理由。

**已補上一對真的咬住的**（`張鐵雄 ←→ 阿海`）：水門焊死庄才保得住，但門一焊，
阿海還在外海的船就再也進不來。阿海的 `prevent` 直接讀張鐵雄的目的——一個成功等於另一個失敗，
和江湖的費彬／劉正風同一個形狀。全員盡力的天花板現在是 **11/12**，11/11 不再是預設結果。
下一步是拿新設定重跑一次真 LLM，看兩場會不會終於分岔。見 `todo.md` 第 1 條。

---

## 快速開始

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[all]"   # 或 .[anthropic] / .[gemini]
copy .env.example .env      # 填你要用的那一家的 key
```

支援兩家 provider，用全域旗標切換（**預設 `gemini`**，全層 `gemini-3.1-flash-lite`）：

```powershell
.\.venv\Scripts\python.exe -m truman.cli --scenario jianghu run `
  --run-id j2 --ticks 96 --seed 7
```

```powershell
# 不花錢，先確認一切正常
.\.venv\Scripts\python.exe -m tests.smoke
.\.venv\Scripts\python.exe -m truman.cli --scenario jianghu run --run-id dry --ticks 30 --stub
.\.venv\Scripts\python.exe -m truman.cli report --run-id dry

# Demo 前端：回放既有 run（上台推薦），或現場開一場（見下方「Demo 前端」）
.\.venv\Scripts\python.exe -m truman.demo                        # http://127.0.0.1:8765

# 看地圖、快取前綴、對帳模型 ID
.\.venv\Scripts\python.exe -m truman.cli --scenario jianghu map
.\.venv\Scripts\python.exe -m truman.cli tokens
.\.venv\Scripts\python.exe -m truman.cli models

# 出報告：節流率、動手／死亡、對話圖、話題擴散、reflection、成本
.\.venv\Scripts\python.exe -m truman.cli report --run-id j2

# 並排比較兩場以上——直接看它們有沒有真的分岔
.\.venv\Scripts\python.exe -m truman.cli compare --run t1 --run t2

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

### 開跑前檢查（預設開）

真跑之前會先送一個幾十 token 的試探呼叫，把**真的會用到**的每一組（模型, thinking_level）
各驗一次——分層路由那四層，加上每個 agent 自己掛的模型。有一組送不出去就整個不開跑
（`exit 2`），並印出是哪一組、錯在哪。

這是 `j3` 逼出來的：那次 96 拍、576 次呼叫**全部**因為 `thinking_level=medium` 被退，
32 秒「跑完」，零對話零意圖。跑到一半才全軍覆沒也會停（`abort_after_failures=12`），
條件是「從頭到尾一次都沒成功」——跑到一半遇到 429／5xx 是暫時的，交給重試那層，
不會把一個已經跑出東西的 run 砍掉。用 j3 的情境重現：原本跑完 96 拍才回報，現在第 2 拍就停。

### Demo 前端

```powershell
# 1. 確認 .env 有 GEMINI_API_KEY（或 GOOGLE_API_KEY）——現場開跑才需要
# 2. 開 demo（保持這個終端開著；改 static 後要重啟才吃到）
.\.venv\Scripts\python.exe -m truman.demo
```

瀏覽器 `http://127.0.0.1:8765/`：

1. **回放既有軌跡（推薦上台）** — 勾 `j2` / `j2b` / `j2c`（江湖）或 `t1` / `t2`（嵐潮）→ 產出完整回放頁。內容已驗證、不燒 API。
2. **現場開一場** — 真 LLM 預設全日 96 ticks；`stub` 僅測管線（對話會鬼打牆，沒模擬價值）。

現場進度會顯示：時辰、phase、最近對話、**已花時間／平均每 tick／本刻已等／預估剩餘**，
以及**每個人的目的進度與絕技存量**——誰做到了什麼、誰的絕技用掉了幾次、誰還捨不得用。
單一 tick 等 LLM 可能數十秒，進度停住不代表掛了。關掉分頁或關掉 `truman.demo` 會中斷；同時只能跑一場。

| 欄位 | 建議值（現場） |
|---|---|
| run id | 新名字（不能跟 `runs/` 裡已有的撞名） |
| ticks | **96**（一天；頁面上限也是 96）；試水管線可先 12–24 |
| seed | `7` |
| scenario | `jianghu` 或 `tempest` |
| provider | `gemini` |
| cast | 可空；或 `cast/jianghu.json` / `cast/tempest.default.json` |
| stub | **不要勾** |

日誌在 `runs/<run-id>/`，回放 HTML 在 `runs/_demo/<run-id>_replay.html`。粗估全日 **$0.3–0.6**（有快取時）；3.1-flash-lite 門檻高，命中率常偏低、帳單會往上。對帳：

```powershell
.\.venv\Scripts\python.exe -m truman.cli report --run-id j3
# 嵐潮兩場既有實錄（seed 42 / 7，皆 held）：
.\.venv\Scripts\python.exe -m truman.cli report --run-id t1
.\.venv\Scripts\python.exe -m truman.cli report --run-id t2
```

---

## 劇本

| 劇本 | 知識 | 動手 | 要看什麼 |
|---|---|---|---|
| **jianghu**（現役主線） | 對稱 | 是 | 金盆洗手火藥桶；社交＋殺戮動力學、anti-snowball |
| **tempest**（嵐潮） | 對稱 | 否 | 暴潮救村；禮／閘／疏散會不會自己長出來；實錄 `t1`／`t2` |
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

jianghu 的前綴目前約 **3172 tokens**（`truman.cli --scenario jianghu tokens` 可重現）。

| 模型 | 門檻 | 輸入價/MTok | 3172 的前綴 |
|---|---:|---:|---|
| `gemini-2.5-flash-lite` | 2048 | $0.10 | **會快取** |
| `gemini-3.1-flash-lite`（現行預設） | 4096 | $0.25 | 靜默失效 |

**要讓快取生效，該動的是模型，不是 prompt。** 把世界區塊硬撐到 4096 是條走不通的路：
真正還沒寫過的公開背景大概只有 650 tokens，剩下的只能靠複述既有內容湊，那會傷模擬品質。
2.5-flash-lite 門檻低一半、輸入價還便宜 2.5 倍，同一份前綴直接進得了快取——
代價是格式穩定度較差（當初換到 3.1 就是為了這個）。

也可以只換佔呼叫數大宗的那一層：`--model routine=gemini-2.5-flash-lite`，
reflect 留在 3.1；或反過來 `--model reflect=gemini-3.5-flash` 換較強的推理。

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

詳見 [`todo.md`](todo.md)。最貼主線的一條：

- **目的與絕技還沒實跑驗證**：LLM 會不會在對的時機把工具拿出來？配了卻從不用，
  代表說明沒寫清楚或那門功夫對他的目的沒用；每次都用，代表配額給太鬆。
  `report` 的絕技表就是為了看出這件事，但還沒有真 LLM 的數據可看。

其餘：

- **共識塌縮**：跑久了可能互相附和。箱庭靠對立人設與場所競爭撐張力。
- **記憶膨脹**：`memory_cap=400`；reflection 永不丟。
- **replay 一致性**：依賴 `(tick, agent, 用途)` key；改節流或人員後舊日誌會對不上。
- **尋仇的 fork 韌性**：runtime inject 不進 WorldState；checkpoint 卡在死亡與觸發之間可能漏報。
- **回放頁的絕技清單取自劇本模組**：用 `--cast` 換過配裝的話，角色卡上那張清單會是劇本
  預設。實際使出來的絕技與目的判定不受影響——那些讀的是日誌。
  （世界區塊的公開招式名號也一樣取自劇本模組，但那是刻意的：它講的是江湖上的名號，
  不是今天到場名單，而且 system[0] 在不同 cast 之間保持一致，快取與對照才成立。）
- **預設模型的快取門檻跨不過**：前綴目前 3172 tokens，`gemini-3.1-flash-lite` 門檻
  4096，靜默失效。**這要靠選模型解決，不是靠把 prompt 撐大**——`gemini-2.5-flash-lite`
  門檻只有 2048、輸入價還便宜 2.5 倍，同一份前綴就進得了快取。取捨是 2.5 的格式
  穩定度較差（當初換到 3.1 就是為了這個）。見 `todo.md`。

## 檔案地圖

```
truman/
  config.py              時間制度、模型路由、成本推導、節流參數
  world/grid.py          格子地圖、區域、BFS 尋路
  world/state.py         WorldState / AgentState（完全可序列化）
  world/observation.py   每 agent 的過濾投影 ← 導演的掛載點
  world/arts.py          絕技目錄（＝這個世界的 tool）：說明、使用時機、配額
  world/goals.py         目的判定器（八個，純函式、不呼叫 LLM）
  world/engine.py        tick 迴圈、intent 驗證、對話追加輪、戰鬥、絕技、目的判定
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
art/…                    立繪／場景圖產生與內嵌
cast/build_editor.py     人物工作室
replay/build_frames.py   事件日誌 → 回放 HTML
todo.md                  主線待辦與實跑紀錄
```

## 人物立繪與場景美術

```powershell
.\.venv\Scripts\python.exe art\gen_portraits.py            # 已存在的跳過
.\.venv\Scripts\python.exe art\gen_portraits.py --force    # 全部重畫
.\.venv\Scripts\python.exe art\embed_scenes.py             # 看場景圖內嵌後多大
```

立繪一致性靠：**共用同一段 `STYLE`**（一字不改）＋ 風格錨當參考影像。
場景圖在 `art/scenes/jianghu/`（關鍵美術＋九個地點），`build_frames.py` 會自動內嵌：
片頭用 `keyart`、片尾用 `night`、角色卡「現在在」顯示對應地點圖。
絕技圖示在 `art/icons/jianghu/`（十五門各一張），角色卡／事件流／工作室／Demo 看板都會用。
事件流小圖示在 `art/icons/events/`（說話／動手／死亡／想通了／世界／互動／目的達成與落空）。
海晏鎮立繪在 `art/portraits/hakoniwa/` 與 `art/portraits/seahaven/`（同一批六人）。
Demo 落地頁另從 `truman/demo/static/art/` 讀靜態檔。沒圖時退回像素城／像素立繪／通用絕技小圖。

畫完重跑 `replay/build_frames.py` 與 `cast/build_editor.py`。

```powershell
.\.venv\Scripts\python.exe art\embed_icons.py              # 看絕技圖示內嵌後多大
.\.venv\Scripts\python.exe art\embed_event_icons.py        # 事件流小圖示
.\.venv\Scripts\python.exe art\embed_portraits.py hakoniwa # 海晏鎮立繪
```

## 開場之前：人物工作室

```powershell
.\.venv\Scripts\python.exe cast\build_editor.py jianghu   # 產出 cast_editor.html
```

改名字／人設／武功／起始位置／在意的人／長相／**今天要做到的事**／**配備的絕技**，
下載 `cast/jianghu.json` 後：

```powershell
.\.venv\Scripts\python.exe -m truman.cli --scenario jianghu run `
  --run-id j2 --ticks 96 --seed 7 --cast cast\jianghu.json
```

絕技那一區是一門一張卡，點下去就是配上或拿掉；卡片上的說明與滑鼠移上去的「什麼時候該用」
**就是角色真正讀到的那段字**（同一份 `ArtDef`，不是另抄一份），所以工作室上看到什麼，
模型就看到什麼。「出門前檢查」會擋掉指到不存在的人或地方的目的——那種錯不會報錯，
只會讓判定永遠不成立，比報錯難查得多。

### 每個人可以掛不同的腦袋

```json
{"id": "fei_bin", "llm": {"model": "gemini-3.5-flash", "temperature": 0.15}}
```

不設就走分層路由。這是最乾淨的對照：**同劇本、同 seed，只換一個人的腦袋**。
報表會把自帶模型的呼叫另外分桶計價（`routine·gemini-3.5-flash`）。

劇本 `.py` 一個字都不會動——設定檔只在那一次 run 生效。`cast/<劇本>.default.json`
是原設定，方便 diff。
