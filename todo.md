# TODO

專案目標已從「單主角楚門式覺察實驗」轉向「箱庭小鎮：把 AI 村民放進去，看會發生什麼」。
最新方向是可殺人的武林箱庭（`scenarios/jianghu.py`，參考金庸《笑傲江湖》）。

## 待辦（依重要性）

### 1. 強者通吃 —— 最傷模擬價值
先手優勢太大：`jianghu` 實跑（j1b）費彬六戰全勝、零反抗、無翻盤、無聯手，
一整天六人死五、只剩費彬一人。擊殺是「重傷→致命」兩拍，受害者第一拍重傷後
跑不動也打不還，第二拍必死。

- [ ] 給「重傷者拼死一擊」加成（背水一戰），或旁觀者見殺戮後 skill 臨時上升（義憤）
- [ ] 讓連續擊殺有代價，而不是零阻力清場
- 動手處：`truman/world/engine.py` `_resolve_attack`

### 2. 社會後果缺失
死亡目前不觸發任何反應。費彬連殺五人零成本，沒有人來討公道——
這違反劇本 NORMS 自己寫的「江湖上沒有白死的人」。

- [ ] 死亡時把「尋仇」意圖注入死者的師門/親友/朋友
- 動手處：`_resolve_attack` 寫 death 事件時，或 director 層監聽 death

### 3. 語言漂移
`gemini-3.1-flash-lite` 在長上下文 + 高殺戮密度下，reflection 會從中文切成英文
（j1b 費彬 t77/t90、曲洋 t55/t70 全英文）。不影響引擎，但報表中英混雜。

- [ ] prompt 明確要求 thought / insight / belief 一律用中文
- 動手處：`truman/llm/prompts.py` MECHANICS 或 reflection_message

### 4. 箱庭（hakoniwa）還沒實跑一整天
和平劇本至今只用 stub 乾跑過。梅姨的小聚引信訂在 tick 72（18:00），
要跑滿 96 tick 才看得到傍晚成不成局。

- [ ] 實跑 `hakoniwa` 96 tick（約 $1.2），看小聚有沒有傳開、傍晚咖啡館有沒有人

## 已完成（本輪）

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
