# TODO

專案目標已從「單主角楚門式覺察實驗」轉向「箱庭小鎮：把 AI 村民放進去，看會發生什麼」。
最新方向是可殺人的武林箱庭（`scenarios/jianghu.py`，參考金庸《笑傲江湖》）。

## 待辦（依重要性）

### 4. 重跑 jianghu，驗證三機制真的破了「強者通吃」
smoke 只驗機制單元對不對（背水命中率、義憤 +2、尋仇注入），**沒驗涌現結果**。
要重跑一整天（tick 0–96）看：費彬還會不會六戰全勝、曲洋收到噩耗後會不會真去尋仇、
中英夾雜有沒有壓下去。跟 j1/j1b 對照。

- [ ] `python -m truman.cli --provider gemini run jianghu --ticks 96 --seed 7`（約 $1）

### 5. 箱庭（hakoniwa）還沒實跑一整天
和平劇本至今只用 stub 乾跑過。梅姨的小聚引信訂在 tick 72（18:00），
要跑滿 96 tick 才看得到傍晚成不成局。

- [ ] 實跑 `hakoniwa` 96 tick（約 $1.2），看小聚有沒有傳開、傍晚咖啡館有沒有人

### 未做的取捨（記著，別忘了）
- **尋仇的 fork 韌性**：`add_runtime` 注入不進 WorldState。全程 replay 會重生（確定性），
  但若 checkpoint 剛好卡在死亡拍與觸發拍之間（12 拍對齊），fork 會漏掉那一則待發尋仇。
  跟 CLI 注入同樣的已知限制。真要補，改成同時寫一筆高 importance 記憶。
- **派系尋仇**：目前只認 kin 明線。五嶽劍派「同氣連枝」沒做成自動尋仇——費彬殺劉是
  左冷禪授意的「清理門戶」，全派自動尋仇會抹掉這層政治張力。留白是刻意的。

## 已完成（本輪）

- **回放畫面（Smallville 風）**：把 j1+j1b 整個模擬日做成自帶資料的互動網頁
  `jianghu_replay.html`（也發成 claude.ai Artifact）。頂視衡山城地圖 + 六個角色做成
  刻字棋子（人如棋子、江湖如局），逐 tick 走位/對話氣泡/動手見血/死亡，右側六人上帝視角
  （傷勢/存歿/武功/死於誰手）+「此刻心裡話」面板 + 時間軸拖曳（死亡刻有 💀 標記）。
  資料由 `scratchpad/build_frames.py` 從事件日誌重建逐 tick 座標（用同一套 BFS 重放 move_to，
  對 8 個 checkpoint 驗證）。要換別場 run 重跑這支即可。
- **逐 tick snapshot 事件**（`engine.tick()`）：每 tick 記全員座標/傷勢/義憤/生死，一行 JSON。
  未來的 run 直接有走位資料，回放不必再重建。
- **強者通吃三機制**（`_resolve_attack`）：先手 +2→+1；「背水一戰」帶傷者攻擊不受
  傷勢拖累、重傷更 +3（守勢仍 -2×傷，成玻璃刀）；「義憤」`fury` 欄位，親眼見殺
  +2（封頂 4），連續擊殺越來越難。見血後在場者一律 `action=None` 重新盤算，
  不會眼睜睜錯過命案。smoke：背水重傷者反傷強者 32/60、義憤上升、下手者自身不漲。
- **尋仇**（`_notify_kin`）：`AgentState.kin` 記在意的人；對方死了，用 director
  runtime inject 在下一拍把噩耗＋尋仇塞進親友眼前（觸發 needs_llm），人在城另一頭
  也傳得到。親眼看見的親友不重複報信。jianghu 目前只連劉正風↔曲洋知音一條線，
  其餘死亡靠義憤發酵。無親友者被殺不生尋仇注入（有測試守住）。
- **鎖中文**：MECHANICS 加「thought/plan 一律中文」、reflection_message 加
  「insights/beliefs 全中文」。都在共用區塊/罕跑的 reflect，成本可忽略。
- 世界引擎支援 `attack`：skill/wound/killed_by、引擎判定勝負、隨機源綁 (seed,tick,攻,守) 保 replay、死亡不可逆、擊殺兩拍不一擊斃命
- combat 徹底 gating：和平劇本的 schema enum 與世界區塊都不含動手
- prompt 拆層：SETTING/EXAMPLES 跟劇本，MECHANICS/TONE 共用
- 三本劇本：seahaven（原楚門，未動）、hakoniwa（和平箱庭）、jianghu（武林）
- report 新增：動手/死亡、誰跟誰說話、話題擴散、聚在一起
- 覺察偵測改成「沒有主角就整層不存在」
- 工程修正：max_output_tokens 900→1600、聽力射程 bug、駁回回饋進 observation、
  收工強制評審走 CLI 路徑、哨兵封頂 10.0

## 實跑紀錄

- `runs/j1` — jianghu tick 0–48（午後），$0.46，劉正風/田伯光死
- `runs/j1b` — fork j1 tick 48→96（亥時），$0.14，令狐沖/曲洋/儀琳死，只剩費彬
