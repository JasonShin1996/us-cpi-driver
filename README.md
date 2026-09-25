# US CPI Driver — 美國 CPI 貢獻度拆解

把美國 CPI 的年增率（YoY）與月增率（MoM）拆成各細項的**貢獻度**（百分點），
回答「這個月 CPI 3.4%，其中多少是能源、多少是核心服務推上去的？」。

- **網站**：<https://jasonshin1996.github.io/us-cpi-driver/>（中文 / English）
- **計算方法**：<https://jasonshin1996.github.io/us-cpi-driver/methodology.html>
  （原始檔 [web/methodology.html](web/methodology.html)，包含所有公式、資料來源與驗證）

成果是一個仿 Bloomberg「Contributions to US CPI」的互動網頁：堆疊柱狀圖、總體與核心
CPI 折線、熱區資料表，資料從 1990 年起。數字已與 Bloomberg 逐項對帳一致。
BLS 每次公布 CPI 後，GitHub Actions 會自動更新資料並重新發布網站。

---

## 1. 運作方式

```
                 ┌──────────── GitHub Actions（平日每天 1 次）────────────┐
BLS 公布時間表 ──► schedule.py check ── 有新月份？ ──否──► 結束            │
                 │        │是                                           │
BLS 權重表 ──────► ri_official.py --if-missing（缺表時才下載）            │
BLS API ─────────► cpi_contrib.py  → web/data/*.json / *.js / *.csv       │
                 │        │                                             │
                 │ checks.py（30 項自我檢查，不過就不發布）                  │
                 │        │                                             │
                 │ git commit web/data  ──►  pages.yml ──► GitHub Pages   │
                 └────────────────────────────────────────────────────────┘
```

沒有後端：網頁是靜態檔，讀 `web/data/cpi_data.js`。其他程式要用數據，可以直接讀
`https://jasonshin1996.github.io/us-cpi-driver/data/cpi_data.json` 或同目錄下的 CSV。

## 2. 檔案結構

```
cpi_contrib.py           主程式：抓 CPI → 權重 → 貢獻度 → web/data/，並產生 standalone 網頁
ri_official.py           下載並解析 BLS 官方 relative importance（每年 12 月的權重表）
schedule.py              BLS CPI 公布時間表 → release_schedule.json；判斷是否有新資料
checks.py                發布前的自我檢查
release_schedule.json    BLS 公布時間表（自動更新）
ri_official.csv          官方 12 月權重，程式用到的 14 個節點（自動產生）
ri_official_full.csv     官方權重表全部細項，CPI-U 與 CPI-W（自動產生）
ri_official/             從 bls.gov 下載的原始檔（xlsx / txt / htm / zip）
web/
  index.html             儀表板（中英雙語）
  methodology.html       計算方法（中英雙語）
  data/cpi_data.json|js  前端資料
  data/cpi_contrib_{basic4,detail7}_{yoy,mom}.csv   貢獻度長表
  data/ri_official_vs_estimated.csv                  官方權重 vs 反推權重逐年對照
.github/workflows/
  update.yml             排程更新資料
  pages.yml              發布 web/ 到 GitHub Pages
  README.md              兩個流程檔的白話說明
```

本機才有、不進 git：`cache/`（BLS API 快取）、`cpi_dashboard_standalone.html`
（資料內嵌的單一 HTML，可直接寄給別人）。

## 3. 本機使用

```bash
pip install -r requirements.txt        # numpy, openpyxl
python cpi_contrib.py                  # 抓最新 CPI、重算、寫入 web/data/，產生 standalone 網頁
python checks.py                       # 自我檢查
python -m http.server -d web 8000      # 預覽網站 → http://localhost:8000
```

其他指令：

```bash
python ri_official.py --download       # 重新下載全部官方權重表
python schedule.py --download check    # 更新公布時間表，並顯示是否有新資料
python cpi_contrib.py --no-ri-file     # 不用官方權重，全部由指數反推（研究比較用）
```

程式從哪個目錄執行都可以，輸出一律寫在專案資料夾內。

## 4. 自動更新（GitHub Actions）

**什麼時候跑**：平日每天一次，14:30 UTC（美東夏令 10:30、冬令 09:30，都在 BLS 08:30 公布後一小時以上）。
每次先用 `schedule.py check` 比對「BLS 已公布的最新月份」與「網站上的最新月份」，
沒有新資料就結束，所以大部分執行只花幾秒。資料一年只更新 12 次，公布當天如果失敗，
隔一個平日會自動補上（網站最多晚一天）。

**每一步做什麼**：

1. 需要時才更新公布時間表（已知的未來公布日少於 3 個、或當天有新資料要更新時，才抓
   <https://www.bls.gov/schedule/news_release/cpi.htm>；失敗就用 repo 裡的版本）
2. 缺前一年 12 月權重表時才下載（bls.gov 可能擋雲端 IP；失敗就沿用 repo 裡的表，當年權重改用反推並在網頁註明）
3. `cpi_contrib.py` 重算
4. `checks.py` 自我檢查，任一項失敗就停止，不會發布，GitHub 會寄信通知
5. commit `web/data/` 等變更，接著發布網站

**手動觸發**：GitHub → Actions → Update CPI data → Run workflow（勾 `force` 可在沒有新資料時也強制重算）。

**Secrets**（Settings → Secrets and variables → Actions，都是選填但建議設定）：

| 名稱 | 用途 |
|---|---|
| `BLS_API_KEY` | BLS API 金鑰（免費：<https://data.bls.gov/registrationEngine/>）。沒有金鑰時每個 IP 每天只能 25 次請求，GitHub 的機器是共用 IP，很容易撞到上限 |
| `BLS_USER_AGENT` | 下載 bls.gov 檔案時的識別字串。bls.gov 要求其中要有 email，例如 `us-cpi-driver you@example.com` |

## 5. 網頁

- **Transform**：MoM%（季調，對上月）/ YoY%（未季調，對去年同月）
- **Breakdown**：四大類（食物、能源、核心商品、核心服務，與 Bloomberg 相同）/
  七細項（在家食物、外食、能源商品、能源服務、核心商品、住宅租金、核心服務不含住宅）
- **精確加總**：預設和 Bloomberg 一樣保留 residual；按下後改用鏈式分解，各項加總剛好等於總體
- **語言**：右上角切換中文 / English，會記在瀏覽器裡
- **網址會記住目前設定**，例如 `index.html#lang=zh&tf=mom&bk=detail7&rg=1Y&exact=0`
- 右上角顯示資料月份與下次公布時間；資料落後超過 2 個月時會出現紅字提醒
- Export CSV / SVG 按鈕目前隱藏；要打開，把 [web/index.html](web/index.html) 裡兩個按鈕的
  `hidden` 屬性拿掉即可，功能程式碼都還在

## 6. 計算方法摘要

完整公式見 [methodology.html](web/methodology.html)。重點：

- **權重**：用 BLS 官方每年 12 月的相對重要性表當錨點（1987 年起），年度內用 BLS 的價格更新式逐月滾動。
  12 月一律用「新基礎」權重，因為它是隔年 1 月月增率與下一個 12 月年增率的基期。
- **反推權重**：沒有官方表時的備援，從指數以最小平方解出。2007 年後與官方差距約 ±0.05pp；
  2007 年前指數只有一位小數，誤差可達 1–4pp，所以只當備援和檢查工具。
- **貢獻度**：基期相對重要性 × 細項期間漲幅。MoM 用季調指數、YoY 用未季調指數。
- **Residual**：各項加總與公布值的差，主因是年增率跨過一月的權重更新
  （12 月年增率不跨更新，residual 為 0）。「精確加總」用逐月鏈式分解把它消除。
- **缺漏月份**：2025 年 10 月未發布；2025-11 的月增率是兩個月的變動。

## 6.1 官方權重資料：來源與用法

BLS 發布的權重相關資料有好幾種，本專案都查過，以下是各自的角色。
下載由 `ri_official.py --download` 自動完成，原始檔放在 `ri_official/`。

| 來源 | 網址 | 內容 | 本專案怎麼用 |
|---|---|---|---|
| **12 月 Table 1（新基礎）** | [總覽頁](https://www.bls.gov/cpi/tables/relative-importance/home.htm)；2020 年起每年一份 `YYYY.xlsx` / `YYYY.htm`；1987–2019 年在 `ri-archive-*.zip` 內（純文字 `YYYY.txt`，例如 [2000.txt](https://www.bls.gov/cpi/tables/relative-importance/2000.txt)） | 每年 12 月、全美城市平均、所有細項的相對重要性（%）。已經是下一年度的新權重 | **主要來源**：權重年 Y+1 的錨點。解析成 `ri_official.csv` |
| **舊權重版（old weights）** | `old-weights-YYYY.htm`（2021 年起）；2001、2003、2007 年的 TXT 與 2009–2019 年奇數年的 PDF 在壓縮檔內 | 同一個 12 月，但仍用當年的舊權重，也就是「12 月的第二套權重」 | **驗證**：把官方上一年 12 月表用價格更新式滾一年，與這張表比對（誤差 2021 年起 ≤ 0.001pp） |
| **每月新聞稿 Table 1** | <https://www.bls.gov/news.release/cpi.t01.htm>（每月 CPI 新聞稿） | 「上個月」的相對重要性，例如 8 月新聞稿列的是 7 月權重 | **驗證逐月權重**：2026 年 7 月的食物 13.540、能源 7.347、核心商品 18.842、核心服務 60.272、在家食物 8.232、能源商品 4.044、能源服務 3.303，與本專案算出的值完全相同 |
| Cost weights | [cost-weights.htm](https://www.bls.gov/cpi/tables/relative-importance/cost-weights.htm)（`cpi-u-historical-cost-weights.xlsx`，2011 年 12 月起） | 以金額表示、可以直接相加的權重 | **已下載、未使用**。BLS 的算法是「全體成本權重 × 官方相對重要性」，資訊和 12 月表相同。BLS 也註明它在正式系統外計算、錯誤風險較高，而且只能在單一權重年度內使用 |
| 權重更新比較頁 | `weight-update-comparison-YYYY.htm` | 同一個月用新權重與舊權重各算一次的「指數」 | 已下載、未使用（是指數，不是權重） |
| 1947–1986 歷史權重 | `historical-relative-importance-1947-1986.xlsx` | 1987 年以前的相對重要性，分類與現在不同 | 已下載、未解析（本專案從 1990 年起算） |
| FRASER 掃描檔 | <https://fraser.stlouisfed.org/title/5240> | BLS 早年的 Relative Importance 公報（掃描 PDF） | 未使用 |

**只需要哪幾列**：程式用到的是 14 個細項（清單見 [methodology.html](web/methodology.html) 第 2.1 節）。
四大類要 Food、Energy、Commodities less food and energy commodities、Services less energy services；
七細項再加 Food at home、Food away from home、Energy commodities、Energy services、Rent of shelter、
Services less rent of shelter。解析時一律用**細項名稱**比對，因為代碼改過（例如 Food 在 1987 年是 `SA11`，現在是 `SAF1`）。

**關於 bls.gov 擋自動抓取**：bls.gov 會擋「假裝成瀏覽器」或沒有聯絡資訊的程式請求（實測回 403），
但接受附上聯絡 email 的識別字串（例如 `us-cpi-driver you@example.com`），所以程式可以自動下載，
不必手動從瀏覽器存檔。GitHub Actions 上也實測可用。

**公布時間**：新一年的 12 月表與 cost weights 由 BLS 在 1 月隨權重更新一起公布
（依 cost weights 頁面說明，可能因人力延後）。

## 7. 驗證

2026-03 YoY 與 Bloomberg 相比：總體 3.256、核心 2.595、食物 0.366、能源 0.791、核心商品 0.229
完全一致，核心服務 1.847 vs 1.848；權重 13.681 / 6.312 / 19.367 一致，60.639 vs 60.640。
2026-07 MoM 逐項相同。另外，BLS 每月新聞稿 Table 1 公布的 2026 年 7 月權重，與本專案逐月滾出的權重完全相同（見 §6.1）。
Bloomberg 這組數字寫在 `checks.py` 裡，每次更新都會重新比對
（年增率用的是未季調資料，不會被修訂，可以一直當基準）。

## 8. 維護須知

- **不用每月手動做事**：新資料由 Actions 自動處理。
- **Actions 失敗時**：到 Actions 頁面看是哪一步。常見原因是 BLS API 限流（設 `BLS_API_KEY`），
  或 BLS 改了網頁或表格格式（`schedule.py`、`ri_official.py` 的解析要跟著改）。
- **每年一次**：確認當年 1–2 月的 Actions 有抓到前一年 12 月的權重表（`ri_official.csv` 會多出
  新一年）；如果 bls.gov 擋了雲端 IP，就在本機跑 `python ri_official.py --download` 後 push。
- **BLS 每年 2 月會修訂過去 5 年的季調資料**，所以舊月份的 MoM 貢獻度可能小幅改變；
  YoY（未季調）不受影響。git 歷史保留了每一版資料。
- **2026 年 10 月的年增率**沒有基期（2025 年 10 月未發布），屆時要依 BLS 的處理方式調整。
- **新增拆法**：在 `cpi_contrib.py` 的 `NODES`、`SPANNING_SYSTEMS`、`BREAKDOWNS` 加上即可，
  網頁會自動多一個 Breakdown 按鈕；新細項若要用官方權重，也要在 `ri_official.py` 的 `NODE_NAMES` 加上名稱。

資料來源：U.S. Bureau of Labor Statistics — CPI-U（api.bls.gov）；
Relative importance of components（<https://www.bls.gov/cpi/tables/relative-importance/home.htm>）；
CPI release schedule（<https://www.bls.gov/schedule/news_release/cpi.htm>）。
