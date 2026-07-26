# truman — 單主角楚門式 LLM 社會模擬

一座 24×16 的格子小鎮，六個 LLM agent 住在裡面。其中五個知道這是攝影棚，一個不知道。

要測的東西只有一個：**主角要多久、透過哪些線索，才會開始懷疑？**

---

## 快速開始

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[all]"   # 或 .[anthropic] / .[gemini]
copy .env.example .env      # 填你要用的那一家的 key
```

支援兩家 provider，用全域旗標切換（預設 `anthropic`）：

```powershell
.\.venv\Scripts\python.exe -m truman.cli --provider gemini run --run-id g1 --ticks 48
```

```powershell
# 不花錢，先確認一切正常
.\.venv\Scripts\python.exe -m tests.smoke                        # 離線煙霧測試
.\.venv\Scripts\python.exe -m truman.cli run --run-id dry --ticks 30 --stub
.\.venv\Scripts\python.exe -m truman.cli report --run-id dry     # 整條管線走一遍

# 看地圖、看快取前綴夠不夠大、對帳模型 ID 有沒有過期
.\.venv\Scripts\python.exe -m truman.cli map
.\.venv\Scripts\python.exe -m truman.cli tokens
.\.venv\Scripts\python.exe -m truman.cli models

# 跑 48 個 tick（8 模擬小時）
.\.venv\Scripts\python.exe -m truman.cli run --run-id demo --ticks 48

# 出報告：節流率、覺察軌跡、reflection、被駁回的 intent、成本
.\.venv\Scripts\python.exe -m truman.cli report --run-id demo

# 零成本重放（讀日誌，不呼叫 API）
.\.venv\Scripts\python.exe -m truman.cli replay --run-id demo

# 反事實分支：從最新 checkpoint 岔出去，注入一個事件
.\.venv\Scripts\python.exe -m truman.cli fork --from-latest demo --run-id demo_b --ticks 24 `
    --inject "chen_yuan:（你在抽屜深處找到一張沒見過的船票。）"
```

---

## 架構

```
Director ── 只改「誰能觀察到什麼」，不改世界狀態
   │
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
   驗證失敗會把錯誤寫回它的記憶（「我想去火星基地，但這座鎮上沒有這個地方」），
   否則它會一直重複同一個幻覺。
2. **Observation 是世界狀態的過濾投影。** 導演的所有操縱都掛在這一層。
   世界引擎仍是唯一權威，所以分支重跑一切可重現。
3. **離散 tick + action queue。** lockstep 好 debug、好 replay，對 prompt cache 友善。
4. **全量日誌從第一天就有。** 沒有它就不能 replay；不能 replay 等於每次分析都要重燒一次錢，
   而且出了有趣現象也重跑不出來。

### 不對稱

這是整個劇本的重點，實作在 prompt 的分層上：

| | system[0] 世界 | system[1] 人設 |
|---|---|---|
| 主角 陳原 | 完全相同 | 沒有任何一句提到節目 |
| 五名演員 | 完全相同 | 明寫「這裡是攝影棚，你在演戲，守則如下」 |

煙霧測試會驗證這件事沒有洩漏（`世界區塊未洩漏演員身分` / `主角人設未洩漏`）。

### 覺察偵測

刻意**不**在主角的 action schema 裡放 suspicion 欄位——那等於每個 tick 都在提示他
「你應該懷疑」，會直接汙染要測的東西。偵測是外部的、事後的，兩層：

- **樣式哨兵**（免費，每 tick）：關鍵詞比對，`SimConfig.suspicion_markers`。
- **LLM 評審**（每 24 tick）：讀主角最近的內心話，給 0–10 分並附原文證據。

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

兩邊共通的是那個最重要的性質:**前綴低於門檻就靜默失效**,不報錯、不多收錢、
帳單上看不出來。所以 `report` 一定要看「快取讀」欄位。

`Call.system_blocks` 的順序在兩邊都一樣重要(世界在前、人設在後),
只是 Anthropic 拿它切斷點,Gemini 拿它接字串。

> ⚠️ **模型 ID 與價格查證於 2026-07-23。** 這些會變 —— Gemini 2.0 Flash 已於
> 2026-06-01 關閉,硬編過期 ID 就是 404。跑之前用 `truman.cli models` 對一次帳,
> 它會標出設定裡哪個 ID 已經查無此模型。`config.py` 裡標 `*` 的快取門檻是我依世代
> 推定的,官方只列了四個模型的值。

## 成本

三個機制在 `truman/llm/base.py`（provider 共用）：

**1. 節流閥（最大槓桿）** — `cognition.needs_llm`。多數 tick 沒事發生，agent 只是在走路，
那些 tick 一次 LLM 都不叫。只有下列情況才思考：動作做完、被搭話、聽見對話、
看到新面孔、導演事件，或每 6 tick 的保底。report 會印出實際節流率。

**2. 循序暖機（只在划算時做）** — 同一個 tick 平行送 N 個共享前綴的請求，會 N 個全部付全價：
快取要等第一個 response 開始 streaming 後才可讀。所以 `run_batch()` 會先送一個、等它回來，
其餘才並行。

但這只有在**前綴真的進得了快取**時才划算。前綴低於門檻時快取靜默失效，暖機那一趟就是
每個 tick 白白多一次序列往返——96 個 tick 大約讓整場 run 慢一倍。所以 `_worth_warming()`
會先比對前綴長度與該模型的門檻，進不去就整批並行（日誌裡會寫一筆 `warmup_skipped`）。
每個 agent 可以自帶模型，所以是**依模型分組**各自判斷：拿 A 模型暖機救不了 B 模型的前綴。

**3. 分層路由** — routine=Haiku 4.5、dialogue/judge=Sonnet 5、reflect=Opus 4.8。
主角有 `protagonist_min_tier` 保底，永遠不走最便宜那層。

### 快取門檻:實測結果

我們的前綴約 **2200 tokens**（保守下界估算，真值通常更高）。兩家的門檻恰好是同一組數字：

| 層級 | 內容 | tokens | 2048 門檻 | 4096 門檻 |
|---|---|---:|---|---|
| 共用 | 世界（全 agent 相同） | ~1999 | 差一點 | ✗ |
| 每人 | 世界＋人設 | ~2150–2250 | ✓ 會快取 | ✗ 靜默失效 |

落在哪一檔決定了預設路由：

| | 2048 門檻（跨得過） | 4096 門檻（跨不過） |
|---|---|---|
| Anthropic | Sonnet 5 | Haiku 4.5、Opus 4.8 |
| Gemini | 2.5 系列 | 3.x 系列 |

所以 Gemini 這邊的預設**刻意選 2.5 系列**跑高流量的 routine / dialogue —— 便宜而且真的
會快取；只有罕見的 reflect 用 3.5-flash（不快取的代價可忽略）。
Anthropic 那邊的取捨相反，見下。

低於門檻**不會報錯、不會多收錢**，只是沒有快取效益——帳單上看不出來，
只能靠 `usage.cache_read_input_tokens` 驗證，所以 report 有這一欄。

我一度打算把 routine 層改路由到 Sonnet 5，理由是「快取後的 Sonnet 輸入價 $0.30/MTok
比未快取的 Haiku $1.00 便宜」。算完是錯的，漏掉輸出價的權重：

```
Haiku 未快取 : (P+V)·1  + O·5       （P=前綴, V=每 tick 變動, O=輸出，單位 $/MTok）
Sonnet 快取  :  P·0.3 + V·3 + O·15
Sonnet 划算  : O·10 < P·0.7 − V·2   → V=400, O=200 時約 P > 4000
```

但 P 一到 4096，Haiku 自己也開始快取，所以 Haiku 一路領先。**routine 層維持 Haiku。**
等世界簡介隨著劇本長大跨過 4096，快取會自動生效，不需要改程式。

我沒有為了湊門檻去灌水填充世界簡介——那些字每次未命中都要付全價。

### 粗估

6 個 agent、96 tick（一模擬日）、節流率約 50%（約 290 次呼叫）：

| 項目 | Anthropic | Gemini |
|---|---:|---:|
| routine | ~$0.70 `haiku-4-5`（未快取） | ~$0.03 `2.5-flash-lite`（命中） |
| dialogue | ~$0.70 `sonnet-5`（命中） | ~$0.10 `2.5-flash`（命中） |
| reflection（約 24 次） | ~$1.05 `opus-4-8` | ~$0.36 `3.5-flash`（未快取） |
| 覺察評審 | ~$0.04 | ~$0.01 |
| **合計 / 模擬日** | **≈ $2.5** | **≈ $0.5** |

兩邊都是 **reflection 佔大頭**（Anthropic 42%、Gemini 73%），因為它用最貴的模型
且輸出最長。要砍成本，先動 `reflection_threshold`，不是動 routine 層。

> ⚠️ 兩欄都是從價目表推算的，**未經實跑驗證** —— 這台機器上沒有任何一家的憑證，
> 我沒辦法實際跑一次對帳。跑完第一天請看 `report` 的成本表核對。
> Gemini 那欄還多一層不確定：`total_output_tokens` 有沒有已經包含 thought tokens
> 沒有明文，我用 `total_tokens` 反推來判斷（`gemini_client._usage`，有單元測試涵蓋
> 兩種情況）。如果報表和帳單對不上，先查這裡。

---

## 目前的取捨與升級路徑

| 現在 | 為什麼 | 升級路徑 |
|---|---|---|
| 記憶檢索用詞彙重疊（中文 bigram） | 不想在 Phase 1 引入 torch / sentence-transformers | 換掉 `MemoryStream.relevance()` 即可，介面已經留好 |
| importance 用規則給分 | 用 LLM 評分會讓每 tick 呼叫數翻倍 | `cognition.IMPORTANCE` 換成一次批次評分呼叫 |
| 對話是「同 tick 追加一輪」 | 純 lockstep 每輪要 10 模擬分鐘，讀起來很怪；追加輪讓一次交談自然，成本又有上限 | 要更長的對話就放寬 `_burst_targets` 的輪數上限 |
| 視野不被牆擋 | 地圖小，line-of-sight 的複雜度不划算 | `build_observations` 裡加一次 Bresenham |
| 沒有物件系統 | `interact` 用自由文字描述就夠 Phase 1 用 | `WorldState` 加 `objects`，在 `_apply_intent` 驗證 |

## 已知問題

- **共識塌縮**：agent 跑久了可能互相附和、趨同。目前靠對立的人設目標（蘇晴 vs 製作組）
  和資源/場所競爭來製造張力。跑久了要看 report 裡的對話是不是變得空洞。
- **記憶膨脹**：`memory_cap=400` 會丟掉低重要度的舊記憶，reflection 永不丟。
  長跑要觀察檢索品質。
- **replay 的一致性**：replay 依賴 `(tick, agent, 用途)` 這組 key 對位。
  改動節流邏輯或 agent 集合之後，舊日誌就對不上了，會直接拋 `KeyError` 而不是靜默走偏。

## 檔案地圖

```
truman/
  config.py              時間制度、模型路由、成本推導、節流參數
  world/grid.py          格子地圖、區域、BFS 尋路
  world/state.py         WorldState / AgentState（完全可序列化）
  world/observation.py   每 agent 的過濾投影 ← 導演的掛載點
  world/engine.py        tick 迴圈、intent 驗證、對話追加輪
  agents/memory.py       memory stream + 三要素檢索
  agents/cognition.py    節流閥、模型選層、prompt 組裝、記憶寫入
  director/director.py   inject / broadcast / summon / cue
  director/awareness.py  樣式哨兵 + LLM 評審
  llm/prompts.py         三層 prompt（對應三個快取穩定層級）
  llm/base.py            provider 共用：Call / Usage / 循序暖機 / 成本統計 / replay
  llm/client.py          provider 工廠
  llm/providers/         anthropic_client.py（顯式斷點）、gemini_client.py（隱式快取）
  llm/tokens.py          CJK-aware 保守估算 + count_tokens 真值 + 模型清單
  obs/eventlog.py        JSONL 全量日誌
  obs/checkpoint.py      存檔 / 讀檔 / 分支
  cast.py                人物設定檔（--cast）：載入 / 驗證 / 套進世界
scenarios/seahaven.py    地圖、六個人物、導演腳本
tests/smoke.py           離線煙霧測試（不呼叫 API）
web/pixelart.js          共用像素美術（地圖圖磚 + 人物），兩個網頁都注入它
art/gen_portraits.py     用 Gemini 影像模型產生整組風格一致的人物立繪
art/embed_portraits.py   把立繪縮圖壓成 data URI 內嵌進網頁
art/portraits/<劇本>/    產出的立繪 PNG（原圖，約每張 700 KB）
cast/build_editor.py     產生 cast_editor.html（人物工作室）＋ 劇本原設定 JSON
replay/build_frames.py   事件日誌 → jianghu_replay.html（整日回放）
```

## 人物立繪

立繪是用 Gemini 影像模型畫的，整組風格一致：

```powershell
.\.venv\Scripts\python.exe art\gen_portraits.py            # 已存在的跳過
.\.venv\Scripts\python.exe art\gen_portraits.py --force    # 全部重畫
```

一致性靠兩件事，缺一不可：**所有 prompt 共用同一段 `STYLE`**（一個字都不能改），
以及**先畫一張風格錨，之後每張都把錨圖當參考影像餵進去**要求對齊線條與上色——
單靠文字描述絕對對不齊。姿態寫在 `POSE`：姿勢要洩漏這個人今天想幹什麼。

畫完重跑 `replay/build_frames.py` 與 `cast/build_editor.py`，圖會自動縮圖、
壓成 data URI 內嵌進兩個網頁（單檔離線可開）。沒有立繪時網頁會自動退回程式畫的像素立繪。

## 開場之前：人物工作室

跑之前先決定「這場的人是誰」。產生編輯頁（離線可開，不需要伺服器）：

```powershell
.\.venv\Scripts\python.exe cast\build_editor.py jianghu   # 產出 cast_editor.html
```

在頁面上改名字／人設／武功／起始位置／在意的人／長相，按「下載 cast.json」存到
`cast/jianghu.json`，然後用它開跑：

```powershell
.\.venv\Scripts\python.exe -m truman.cli --scenario jianghu run `
  --run-id j2 --ticks 96 --seed 7 --cast cast\jianghu.json
```

### 每個人可以掛不同的腦袋

工作室裡每個角色都能單獨指定 **模型／溫度／思考深度**（存進 cast.json 的 `llm` 欄位）：

```json
{"id": "fei_bin", "llm": {"model": "gemini-3.5-flash", "temperature": 0.15}}
```

不設就照原本的分層路由走（多數 tick 走 routine 的便宜模型，社交場合才升到 dialogue）。
這是最乾淨的對照實驗手段：**同一個劇本、同一顆 seed，只換一個人的腦袋**，
看結局會不會不一樣。報表會把自帶模型的呼叫**另外分桶計價**（`routine·gemini-3.5-flash`），
不然那些呼叫會被按分層預設的價目算，帳直接錯。

> Anthropic 開了 extended thinking 時不能同時給 temperature，這種情況會安靜忽略溫度，
> 不會讓整場 run 掛在那裡。

劇本 `.py` 一個字都不會動——設定檔只在那一次 run 生效，換一份就是另一場實驗。
設定檔有問題（站在牆上、id 重複、人設空白、亂指親友）會在燒掉任何額度之前被擋下來，
一次列出全部問題。`cast/<劇本>.default.json` 是劇本原設定，可以直接拿來跑，也方便 diff。
