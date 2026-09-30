# 資料陷阱與處理方式（BLS CPI）

接手這個專案（人或 AI agent）之前請先讀這份。每一條都是實際踩過、驗證過的，
格式是：**症狀 → 原因 → 目前的處理 → 怎麼發現／重新檢查**。

第一部分是細項頁（`detail/cpi_detail.py`）特有的，第二部分是整個專案都適用的。

---

## 一、細項資料（`detail/cpi_detail.py`）

### 1. BLS Excel 的縮排（level）有錯

- **症狀**：某個母項底下的子項權重加總 ≠ 母項權重；細項被重複計算，或跑到錯的大類。
- **原因**：`YYYY.xlsx` 的 Table 1 用第一欄的數字表示階層，但有幾列標錯。2024、2025 年都有同樣的兩處：
  - `Alcoholic beverages` 被標成 level 3（掛在 Food 底下），應該是 level 2（和 Food 同層）。
    Food 的子項加總會多出 0.84。
  - `Information technology, hardware and services` 整個分支被標高一層（3 應為 4，
    它的子項 4 應為 5），所以被算成 Communication 的直接子項。
    正確結構是 Telephone services 1.466 ＋ 它 1.714 ＝ Information and information processing 3.181。
- **處理**：`repair_levels()` 用「母項權重 ＝ 直接子項權重加總」自動偵測並修正：
  子項不夠時先把後面緊接的同層項目往下收（優先），子項太多時再把最後面的子項往上提。
  **順序很重要**：先試「往上提」會把資訊科技錯誤地提到更上層。
  容許誤差是 `0.002 + 0.0006 × 子項數`（每個權重四捨五入到小數三位）。
- **怎麼發現**：執行時會印出 `fixed indentation: ...`；修不好的會印出
  `! hierarchy does not add up: ...`。**看到這行就代表新一年的表有新的錯法，要人工檢查。**

### 2. Excel 沒有細項代碼，要靠名稱對 `cu.item`

- **原因**：Table 1 的 xlsx 只有細項名稱。指數序列要用代碼（例如 `SEHA`），所以用
  <https://download.bls.gov/pub/time.series/cu/cu.item> 以名稱對應。名稱比對前會先正規化
  （小寫、`&`→`and`、去掉標點與註腳數字）。
- **對不上的（2025 年表）**，寫在 `NAME_OVERRIDES`：
  - `Housing at school, excluding board` → `SEHB01`（cu.item 叫 Lodging while at school）
  - `Technical and business school tuition and fees` → `SEEB04`
    （**不是** `SEEB03`，那是 Day care and preschool；第一次憑印象填錯過）
  - `Care of invalids and elderly at home`：BLS 沒有全美指數，刻意不對應（見第 4 點）
- **怎麼發現**：執行時印出 `! no BLS item code for: ...`。新增對照前務必到 `cu.item` 查證。

### 3. 細項改名，舊年份的權重表對不上

- **症狀**：某些細項在某幾年沒有權重（`w` 是 null），歷史圖斷掉。
- **原因**：BLS 會改細項名稱，舊年份的 12 月表用的是舊名。例如：
  `Airline fare` → `Airline fares`、`Child care and nursery school` → `Day care and preschool`、
  `Land line telephone services` → `Residential telephone services`、
  `Men's furnishings` → `Men's underwear, nightwear, swimwear, and accessories`、
  `Cable and satellite television service` → `Cable, satellite, and live streaming television service`。
- **處理**：`ALIASES`（目前名稱 → 舊名稱清單）。
- **怎麼發現**：對每一年的 12 月表，列出「目前階層有、但該年表裡找不到」的名稱，再和「只出現在舊表的名稱」
  配對。檢查 `detail.json` 裡最底層細項的 `w` 是否有整年 null，是最快的症狀。
  有些改名同時改了涵蓋範圍，權重會小跳一下，屬正常。
- **目前仍缺的**：`Club membership for shopping clubs, fraternal, or other organizations, or participant sports fees`
  在 2016-09 到 2020-11 對不上舊名（舊表的拆法不同），這段期間沒有權重。

### 4. 有權重、但 BLS 不公布指數的細項

- **症狀**：細項加總明顯小於總體，「未分配」偏大。
- **原因**：
  - 22 個 `Unsampled ...` 細項（2025 年合計權重約 1.85%）BLS 不公布指數。
    其中 `Unsampled owners' equivalent rent of secondary residences` 單獨就有約 1.2%，影響最大。
  - `Care of invalids and elderly at home` 沒有全美指數。
- **處理**：用最近一個有指數的上層細項當代理（`proxy=True`），計算權重和貢獻。
  BLS 自己計算時，這類項目也是用上層的變動補值。頁面上要標示「估計」。
- **效果**：年增率「未分配」平均從 0.16pp 降到 0.08pp。

### 5. 小細項不是每個月都有指數

- **症狀**：某些月份某細項沒有漲幅；權重在整年都變成 null。
- **原因**：BLS 樣本不足時不公布。例如 Domestic services 2024 年只有 5 個月有值、
  Gardening and lawncare services 2016 年後約一半月份缺值。不是讀檔漏掉，BLS 原始檔就沒有。
- **處理**：**只為了推算權重**，缺值月份讓它跟著上一層細項變動（`fill`）；
  它自己那個月的漲幅和貢獻保持 null（頁面顯示「無資料」），不捏造數字。
- **注意**：12 月錨點那個月缺值時，整年的權重都算不出來，所以一定要補值。

### 6. 很多細項沒有季調序列

- **原因**：BLS 只對有明顯季節性的序列做季調。最底層 166 項中有 34 項沒有季調序列
  （含代理項目），合計約 11% 權重，包括 `Wireless telephone services`、`Health insurance`、
  `Limited service meals and snacks`。
- **處理**：月增率「有季調用季調，沒有就用未季調」（`sa` 欄位標示）。這是 BLS 彙總季調指數時的做法。
- **注意**：SF Fed 對這類細項的數字和我們略有不同（2026-08 無線通訊：我們 0.097、SF Fed 0.108，
  都換算成對核心 CPI 的貢獻）；排名一致。

### 7. 序列的基期不一定是 1982–84

- **原因**：較新的細項用別的基期（例如 `December 1997=100`）。
- **陷阱**：用 `cu.series` 的 `base_code == 'S'` 篩選，會漏掉這些細項，誤以為它們沒有季調序列
  （第一次檢查時就把 Wireless 誤判成沒有 SA；實際上是 base 篩選造成的，後來確認它真的沒有 SA）。
- **處理**：所有計算只用比值（本月／上月），不需要知道基期，所以不篩 base。

### 8. BLS 整批資料檔的格式

- 來源：<https://download.bls.gov/pub/time.series/cu/>，不需要 API 金鑰、沒有每日次數限制。
- 用到的檔案：`cu.item`、`cu.data.1.AllItems`、八個大類的 `cu.data.11`–`18`，
  以及 `cu.data.20.USCommoditiesServicesSpecial`（**核心 CPI `SA0L1E` 只在這個檔**，不在大類檔裡）。
- 格式：Tab 分隔、欄位有空白填充、行尾是 CRLF。
- 序列代碼：`CUUR0000` + 細項代碼 = 未季調月資料、`CUSR0000` = 季調月資料；
  `CUUS…`（半年資料）與區域代碼不是 `0000` 的都要略過；期間 `M13` 是年平均，要略過。
- 下載同樣需要含 email 的 User-Agent（見第二部分第 4 點）。

### 9. Table 1 有兩段，只能用「支出類別」那段

- xlsx 的 Table 1 分成 `Expenditure category`（互斥的階層）和 `Special aggregate indexes`
  （All items less food、Commodities 等，會和其他項目重疊）。細項頁只取前者。

### 10. 切到 level 5 後，「最底層」不一定在 level 5

- 以 level 5 為上限時，最底層細項分布在 level 2–5（例如某些 level 3 項目本身沒有更細的子項）。
  判斷方式是「後面緊接的列是否更深」，不是看 level 數字。這 166 項互斥且加總約 100。

### 11. 細項層級的年增率「未分配」比主頁大，這不是 bug

- 月增率「未分配」平均 0.019pp；年增率平均 0.079pp、95% 在 0.21pp 以內。
- 原因同主頁的 residual：年增率跨過一月的權重更新，式 (1) 不成立；拆得越細越明顯。
  另外包含當月 BLS 沒公布的細項。
- **權重正確性的檢查方式**：用未季調指數算「單月」貢獻，同一權重年度內加總應等於總體月增率
  （實測平均差 0.012pp，差距大的月份都是有細項缺值，例如 2025-11 約 5% 權重缺值）。

### 12. 外部比對時的定義差異

- **BLS 新聞稿 Table 2**（<https://www.bls.gov/news.release/cpi.t02.htm>）：權重欄是**上個月**的
  相對重要性；漲幅只到小數一位，四捨五入邊界會差 0.1（2026-08 Rent of primary residence：2.75 vs 2.7）。
  名稱和 Table 1 不完全相同，約 56 項能直接對上。
- **SF Fed 資料頁 Excel 的 `chart5_coreCPI_granularCont_MoM`**：是對**核心 CPI** 的貢獻，
  要把我們（對整體 CPI）的數字除以核心權重佔比才能比。2013 年起 6,522 筆比對平均差 0.0005pp。
- **SF Fed 的 Shelter** 是 BLS `Shelter`（`SAH1`），我們主頁用 `Rent of shelter`（`SAS2RS`），
  兩者只差 `Tenants' and household insurance`（2025-12 權重 0.292）。

---

## 二、整個專案都適用

1. **2025 年 10 月 CPI 沒有公布**（聯邦政府停擺）。所有計算只串接有資料的月份：
   2025-11 的月增率是兩個月的變動；**2026-10 的年增率沒有基期**，屆時要依 BLS 的處理方式調整。
2. **12 月有兩套權重**：BLS 公布的 12 月表是「新基礎」（下一年度的籃子），也是隔年 1 月月增率、
   下一個 12 月年增率的基期。12 月的權重一律用新基礎，否則 12 月貢獻加不起來。
3. **2007 年 1 月以前指數只有一位小數**：由指數反推權重會差 1–4pp，所以一律用官方 12 月表當錨點。
4. **bls.gov 擋程式抓取**：瀏覽器樣式的 User-Agent 或沒有聯絡資訊的會被拒（403）；
   User-Agent 裡要有 email（例如 `us-cpi-driver you@example.com`）。只放 repo 網址也會被擋。
   GitHub Actions 用 Secret `BLS_USER_AGENT` 設定。
5. **BLS API 沒有金鑰時每個 IP 每天 25 次**，GitHub Actions 是共用 IP，容易撞上限。
   主頁用 API（建議設 `BLS_API_KEY`），細項頁用整批資料檔（不受限）。
6. **季調資料每年 2 月會修訂過去 5 年**：舊月份的月增率貢獻會小幅改變；年增率（未季調）不會修訂，
   所以 `checks.py` 的 Bloomberg 基準用年增率。
7. **權重表的格式依年份不同**：1987–2019 在 `ri-archive-*.zip` 裡的 TXT（1987–1999 有舊代碼、大寫；
   2000 年後有點線、小數可能省略開頭 0、長名稱會斷行）；2020 年起是 xlsx；舊權重版 2021 年起是 HTML。
   細項代碼隨年份改變（Food 在 1987 年是 `SA11`，現在是 `SAF1`），所以一律用名稱比對。
   2010 年以前的表沒有 `Energy services`，用 Energy − Energy commodities 推得。
8. **GitHub Actions**：
   - 流程用 `GITHUB_TOKEN` 做的 push **不會觸發其他流程**，所以 `update.yml` 要直接呼叫 `pages.yml`。
   - 推送 `.github/workflows/` 的檔案需要 `workflow` 權限（`gh auth refresh -s workflow`）。
   - 公開 repo 連續 60 天沒有 commit，排程流程會被自動停用。
9. **網頁裡寫 TeX 公式**：`<` 會被瀏覽器當成 HTML 標籤開頭（例如 `\sum_{i<n}` 會吃掉後面的內容），
   要寫成 `\lt`。
