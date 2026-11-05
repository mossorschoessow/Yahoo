# 雅虎日拍捡漏监控（Yahoo!オークション AI Monitor）

[繁體中文](README.md) | **简体中文**

参考 [ai-goofish-monitor](https://github.com/Usagi-org/ai-goofish-monitor)（闲鱼 AI 监控）的概念，改写成 **雅虎日拍 + PayPayフリマ** 版本。

定时搜索日拍 → 用条件预先筛选 → 抓商品页（说明、图片、卖家评价）→ 查**近期成交行情** → 交给多模态 AI 判断 → 值得买就推送到手机。

## 跟原项目的差别

| | 原项目（闲鱼） | 这个版本（日拍） |
|---|---|---|
| 抓取方式 | Playwright 开浏览器 + 登录 Cookie | 一般 HTTP 请求（日拍页面内嵌 JSON），**不用登录、不用浏览器** |
| 价格参考 | 自己累积的价格历史 | 直接查日拍「落札相場」（已结束拍卖的成交价），算中位数 / P25 / P75 |
| 商品来源 | 闲鱼 | 日拍拍卖 + 定額 + PayPayフリマ |
| 没有 AI 时 | 不能用 | 自动改用「规则模式」：现价 ≤ 成交中位数 × 70% 就通知 |
| 界面 | Vue 前端 | Windows 桌面程序 / 单页 Web 界面（结果 / 任务 / 日志 / 设置） |
| 通知 | ntfy、企业微信、Bark、Telegram… | Telegram、Discord、ntfy、Bark、通用 Webhook |

## 桌面版（Windows，推荐）

1. 到 [Releases](../../releases) 下载 `YahooAuctionMonitor.exe`，双击运行，不用安装 Python。
   - 第一次运行时 Windows 可能跳出「Windows 已保护您的电脑」，这是因为程序没有付费数字签名：按「其他信息」→「仍要运行」。
2. 打开「**设置**」标签页，填 AI 的 API Key 和至少一种通知方式，按「测试 AI 连接」、「发送测试通知」确认没问题。
3. 到「**任务**」标签页编辑示例任务或添加任务，勾选「启用」。示例任务默认都是停用的。

使用方式：
- **关闭窗口不会结束程序**，程序会缩到右下角系统托盘继续监控。要真的结束，请在系统托盘图标按右键 →「结束」。
- 系统托盘菜单可以「立即运行全部任务」、「打开文件夹」，也可以打开「开机时自动启动」。
- 同时只能开一个；再双击 exe 会叫出已经在跑的窗口。
- 设置、任务和结果都存在 `%LOCALAPPDATA%\YahooAuctionMonitor`，更新 exe 不会丢失数据。
- 窗口使用 Windows 内置的 Edge WebView2（Windows 10/11 都有）。如果没有，会改用默认浏览器打开。
- 右上角可以切换**繁體中文 / 简体中文**；通知内容和 AI 回复也会跟着切换。默认跟随 Windows 的语言。

自己打包 exe：

```bash
pip install -r requirements.txt -r requirements-desktop.txt
python build.py            # 产生 dist/YahooAuctionMonitor.exe
```

或推送 `v1.0.0` 这类标签到 GitHub，Actions 会自动打包并创建 Release（`.github/workflows/release.yml`）。

## 从源代码运行

需要 Python 3.10 以上。

```bash
cd yahoo-auction-monitor
python -m venv .venv
.venv\Scripts\activate          # macOS / Linux 用：source .venv/bin/activate
pip install -r requirements.txt
```

## 设置

可以直接在 Web 界面的「设置」标签页填写，或手动编辑：

1. **`.env`**：把 `.env.example` 拷贝成 `.env`，填 AI 和通知设置（说明都写在文件里）。
   - `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL_NAME`：任何 OpenAI 兼容 API，模型**要能看图**（例如 gpt-4o-mini、Gemini、Qwen-VL 等）。
   - 通知至少设一个，最推荐 **Telegram** 或 **Discord Webhook**（在中国大陆需要科学上网；不方便的话可以用 **ntfy** 或 **Bark**（iOS））。
   - `LOCAL_CURRENCY=CNY`、`JPY_TO_LOCAL_RATE=0.048` 可以在通知里顺便显示人民币（汇率请自行更新）。
2. **`tasks.json`**：监控任务，也可以直接在 Web 界面添加 / 编辑。

## 使用

```bash
python main.py web
```
打开 http://127.0.0.1:8000 ，定时任务会在后台自动运行。

其他命令：

```bash
python main.py search "ニンテンドースイッチ 有機EL"      # 先试搜索，看结果和成交行情
python main.py search "ニンテンドースイッチ 有機EL" --closed   # 看已结束拍卖（成交价）
python main.py run                                        # 所有启用的任务各跑一次
python main.py run -t "Switch 有機EL 主机"                  # 只跑一个任务
python main.py daemon                                     # 不开网页，只在后台跑
```

## 任务字段

| 字段 | 说明 |
|---|---|
| `keyword` | 搜索关键词。**用日文搜索结果多很多**（例：`ニンテンドースイッチ` 而不是 `Switch`） |
| `description` | 用中文写你的需求，AI 会照这个判断 |
| `min_price` / `max_price` | 价格区间（日元，含税）。`min_price` 同时会用来排除行情计算里的配件 |
| `condition` | `any` / `used` / `new` |
| `listing_type` | `any` / `auction`（只看拍卖）/ `buynow`（只看定額） |
| `sort` | `new` 新上架优先（**捡漏首选**）/ `end` 快结束优先（狙击）/ `price` 低价优先 |
| `max_pages` | 每次扫几页（每页 50 笔） |
| `exclude_words` | 标题含这些字就跳过，例如 `ジャンク`、`箱のみ`、`ケース` |
| `require_words` | 标题必须包含的字 |
| `individual_only` | 只看个人卖家 |
| `include_fleamarket` | 是否包含 PayPayフリマ |
| `min_seller_rating` | 卖家好评率下限（%） |
| `max_bids` | 出价人数上限（太多人抢通常捡不到） |
| `max_end_hours` | 只看 N 小时内结束的拍卖 |
| `interval_minutes` | 运行间隔（最少 5 分钟） |
| `notify_min_score` | AI 分数 ≥ 这个值才通知 |
| `deal_ratio` | 规则模式的门槛：现价 ≤ 行情中位数 × 这个比例 |

## 捡漏小技巧

- **新上架（`sort: new`）+ 短间隔** 最容易捡到：定价错误或急售的商品，通常几分钟到几小时内就会被买走。
- **定額 / フリマ** 才是真正的「捡漏」，价格就是成交价；拍卖的现价还会涨，AI 会估计最终成交价。
- 「1円スタート」的拍卖现价只有 1 日元，**会被 `min_price` 挡掉**。想追这种拍卖，另开一个 `sort: end`、`max_end_hours: 3`、`min_price` 留空的任务。
- 日拍很多卖家**不寄海外**，要通过代拍（Buyee、ZenMarket、乐淘等）。**算总成本**时要加：日本国内运费 + 代拍手续费 + 国际运费 + 可能的关税。便宜 2,000 日元的东西，加上国际运费可能就不划算了。
- 标题有 `ジャンク`（不保证能用）、`現状品`（照现况卖）、`ノークレーム・ノーリターン`（不接受客诉退货）时要特别小心。

## 注意事项

- 只用来个人监控，**请不要把间隔调得太短**（默认每次请求间隔 3 秒、任务最短 5 分钟），避免造成日拍服务器负担，也避免被挡。
- 日拍改版时，页面结构可能会变，若出现「找不到 `__NEXT_DATA__`」错误，代表需要更新 `monitor/yahoo.py` 的解析方式。
- AI 判断只是参考，出价 / 购买前请自己再看一次商品页。

## 文件结构

```
main.py               命令行入口
monitor/yahoo.py      日拍 / PayPayフリマ 搜索、商品页、成交行情解析
monitor/runner.py     单一任务流程（筛选 → 商品页 → 行情 → AI → 通知）
monitor/ai.py         AI 分析（OpenAI 兼容、多模态）＋ 规则模式
monitor/notify.py     推送通知
monitor/storage.py    SQLite（data/app.sqlite3）
monitor/scheduler.py  定时调度
monitor/web.py        Web API
static/index.html     Web 界面
prompts/base_prompt.txt        AI 提示词・繁体中文（可自行修改）
prompts/base_prompt.zh-CN.txt  AI 提示词・简体中文
monitor/i18n.py       繁体 / 简体切换
desktop.py            桌面版入口（窗口、系统托盘）
build.py              打包成 exe
```
