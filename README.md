# 雅虎日拍撿漏監控（Yahoo!オークション AI Monitor）

參考 [ai-goofish-monitor](https://github.com/Usagi-org/ai-goofish-monitor)（閒魚 AI 監控）的概念，改寫成 **雅虎日拍 + PayPayフリマ** 版本。

定時搜尋日拍 → 用條件預先篩選 → 抓商品頁（說明、圖片、賣家評價）→ 查**近期成交行情** → 交給多模態 AI 判斷 → 值得買就推播到手機。

## 跟原專案的差別

| | 原專案（閒魚） | 這個版本（日拍） |
|---|---|---|
| 抓取方式 | Playwright 開瀏覽器 + 登入 Cookie | 一般 HTTP 請求（日拍頁面內嵌 JSON），**不用登入、不用瀏覽器** |
| 價格參考 | 自己累積的價格歷史 | 直接查日拍「落札相場」（已結束拍賣的成交價），算中位數 / P25 / P75 |
| 商品來源 | 閒魚 | 日拍拍賣 + 定額 + PayPayフリマ |
| 沒有 AI 時 | 不能用 | 自動改用「規則模式」：現價 ≤ 成交中位數 × 70% 就通知 |
| 介面 | Vue 前端 | Windows 桌面程式 / 單頁 Web 介面（結果 / 任務 / 日誌 / 設定） |
| 通知 | ntfy、企業微信、Bark、Telegram… | Telegram、Discord、ntfy、Bark、通用 Webhook |

## 桌面版（Windows，推薦）

1. 到 [Releases](../../releases) 下載 `YahooAuctionMonitor.exe`，雙擊執行，不用安裝 Python。
   - 第一次執行時 Windows 可能跳出「Windows 已保護您的電腦」，這是因為程式沒有付費數位簽章：按「其他資訊」→「仍要執行」。
2. 打開「**設定**」分頁，填 AI 的 API Key 和至少一種通知方式，按「測試 AI 連線」、「發送測試通知」確認沒問題。
3. 到「**任務**」分頁編輯範例任務或新增任務，勾選「啟用」。範例任務預設都是停用的。

使用方式：
- **關閉視窗不會結束程式**，程式會縮到右下角系統匣繼續監控。要真的結束，請在系統匣圖示按右鍵 →「結束」。
- 系統匣選單可以「立即執行全部任務」、「開啟資料夾」，也可以打開「開機時自動啟動」。
- 同時只能開一個；再雙擊 exe 會叫出已經在跑的視窗。
- 設定、任務和結果都存在 `%LOCALAPPDATA%\YahooAuctionMonitor`，更新 exe 不會遺失資料。
- 視窗使用 Windows 內建的 Edge WebView2（Windows 10/11 都有）。如果沒有，會改用預設瀏覽器開啟。

自己打包 exe：

```bash
pip install -r requirements.txt -r requirements-desktop.txt
python build.py            # 產生 dist/YahooAuctionMonitor.exe
```

或推送 `v1.0.0` 這類標籤到 GitHub，Actions 會自動打包並建立 Release（`.github/workflows/release.yml`）。

## 從原始碼執行

需要 Python 3.10 以上。

```bash
cd yahoo-auction-monitor
python -m venv .venv
.venv\Scripts\activate          # macOS / Linux 用：source .venv/bin/activate
pip install -r requirements.txt
```

## 設定

可以直接在 Web 介面的「設定」分頁填寫，或手動編輯：

1. **`.env`**：把 `.env.example` 複製成 `.env`，填 AI 和通知設定（說明都寫在檔案裡）。
   - `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL_NAME`：任何 OpenAI 相容 API，模型**要能看圖**（例如 gpt-4o-mini、Gemini、Qwen-VL 等）。
   - 通知至少設一個，最推薦 **Telegram** 或 **Discord Webhook**（台灣用起來最順）。
   - `JPY_TO_LOCAL_RATE=0.21` 可以在通知裡順便顯示台幣。
2. **`tasks.json`**：監控任務，也可以直接在 Web 介面新增 / 編輯。

## 使用

```bash
python main.py web
```
打開 http://127.0.0.1:8000 ，排程會在背景自動跑。

其他指令：

```bash
python main.py search "ニンテンドースイッチ 有機EL"      # 先試搜尋，看結果和成交行情
python main.py search "ニンテンドースイッチ 有機EL" --closed   # 看已結束拍賣（成交價）
python main.py run                                        # 所有啟用的任務各跑一次
python main.py run -t "Switch 有機EL 主機"                  # 只跑一個任務
python main.py daemon                                     # 不開網頁，只在背景跑
```

## 任務欄位

| 欄位 | 說明 |
|---|---|
| `keyword` | 搜尋關鍵字。**用日文搜尋結果多很多**（例：`ニンテンドースイッチ` 而不是 `Switch`） |
| `description` | 用中文寫你的需求，AI 會照這個判斷 |
| `min_price` / `max_price` | 價格區間（日圓，含稅）。`min_price` 同時會用來排除行情計算裡的配件 |
| `condition` | `any` / `used` / `new` |
| `listing_type` | `any` / `auction`（只看拍賣）/ `buynow`（只看定額） |
| `sort` | `new` 新上架優先（**撿漏首選**）/ `end` 快結束優先（狙擊）/ `price` 低價優先 |
| `max_pages` | 每次掃幾頁（每頁 50 筆） |
| `exclude_words` | 標題含這些字就跳過，例如 `ジャンク`、`箱のみ`、`ケース` |
| `require_words` | 標題必須包含的字 |
| `individual_only` | 只看個人賣家 |
| `include_fleamarket` | 是否包含 PayPayフリマ |
| `min_seller_rating` | 賣家好評率下限（%） |
| `max_bids` | 出價人數上限（太多人搶通常撿不到） |
| `max_end_hours` | 只看 N 小時內結束的拍賣 |
| `interval_minutes` | 執行間隔（最少 5 分鐘） |
| `notify_min_score` | AI 分數 ≥ 這個值才通知 |
| `deal_ratio` | 規則模式的門檻：現價 ≤ 行情中位數 × 這個比例 |

## 撿漏小技巧

- **新上架（`sort: new`）+ 短間隔** 最容易撿到：定價錯誤或急售的商品，通常幾分鐘到幾小時內就會被買走。
- **定額 / フリマ** 才是真正的「撿漏」，價格就是成交價；拍賣的現價還會漲，AI 會估計最終成交價。
- 「1円スタート」的拍賣現價只有 1 日圓，**會被 `min_price` 擋掉**。想追這種拍賣，另開一個 `sort: end`、`max_end_hours: 3`、`min_price` 留空的任務。
- 日拍很多賣家**不寄海外**，要透過代標（Buyee、ZenMarket、樂淘等）。**算總成本**時要加：日本國內運費 + 代標手續費 + 國際運費 + 可能的關稅。便宜 2,000 日圓的東西，加上國際運費可能就不划算了。
- 標題有 `ジャンク`（不保證能用）、`現状品`（照現況賣）、`ノークレーム・ノーリターン`（不接受客訴退貨）時要特別小心。

## 注意事項

- 只用來個人監控，**請不要把間隔調得太短**（預設每次請求間隔 3 秒、任務最短 5 分鐘），避免造成日拍伺服器負擔，也避免被擋。
- 日拍改版時，頁面結構可能會變，若出現「找不到 `__NEXT_DATA__`」錯誤，代表需要更新 `monitor/yahoo.py` 的解析方式。
- AI 判斷只是參考，下標 / 購買前請自己再看一次商品頁。

## 檔案結構

```
main.py               指令入口
monitor/yahoo.py      日拍 / PayPayフリマ 搜尋、商品頁、成交行情解析
monitor/runner.py     單一任務流程（篩選 → 商品頁 → 行情 → AI → 通知）
monitor/ai.py         AI 分析（OpenAI 相容、多模態）＋ 規則模式
monitor/notify.py     推播通知
monitor/storage.py    SQLite（data/app.sqlite3）
monitor/scheduler.py  定時排程
monitor/web.py        Web API
static/index.html     Web 介面
prompts/base_prompt.txt  AI 提示詞（可自行修改）
```
