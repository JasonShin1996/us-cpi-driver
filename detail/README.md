# 細項貢獻（detail）

細項頁 `web/detail.html` 的資料：把 CPI 沿 BLS 的支出類別階層一路拆到 level 5
（239 個細項，其中最底層 166 個），計算每個細項每個月的權重、漲幅與對整體 CPI 的貢獻。

> **先讀 [PITFALLS.md](PITFALLS.md)**：BLS 資料有不少陷阱（Excel 縮排錯誤、細項改名、
> 未公布指數的細項、沒有季調序列……），裡面寫了每一個的症狀、原因和目前的處理方式。

## 執行

```bash
python detail/cpi_detail.py            # → web/data/detail.json
```

需要先有 `ri_official/`（官方權重表原始檔）與 `ri_official_full.csv`，由根目錄的
`python ri_official.py --download` 產生。BLS 整批資料檔會下載到 `cache/bls_flat/`（12 小時內不重抓）。

| 參數 | 預設 | 說明 |
|---|---|---|
| `--max-level` | 5 | 階層往下展開到第幾層 |
| `--years` | 10 | 可選月份：最近幾年 |
| `--history-start` | 2006-01 | 漲幅歷史從哪個月開始（異常比較的基準要往前 10 年） |

## 中文名稱

`item_names_zh.csv`（欄位 `name,zh`）是 BLS 英文細項名稱 → 繁體中文的對照表，由本專案翻譯，
`cpi_detail.py` 讀進 `detail.json` 的 `zh` 欄位。BLS 新增或改名細項時，執行會印出
`! no Chinese name in detail/item_names_zh.csv for: ...`，把缺的補進 CSV 即可（名稱內有逗號要加引號）。
網頁中文版顯示中文名稱，旁邊附灰色英文原名，搜尋中英文都可以。

## 計算方式

和主頁相同，只是一個細項一個細項做（完整說明見 [methodology.html](../web/methodology.html)）：

1. **12 月錨點**：每年 12 月官方表的相對重要性（新基礎），依細項名稱對應（含舊名稱 `ALIASES`）。
2. **年度內**：`RI_i(t) = RI_i(a) · [I_i(t)/I_i(a)] / [I_all(t)/I_all(a)]`。
3. **月增率貢獻**：`RI_i(上個有資料的月) × 細項月增率`；有季調指數用季調，沒有就用未季調。
4. **年增率貢獻**：`RI_i(t−12) × 細項年增率`（未季調）。
5. **未分配**：總體變動率 − 最底層細項貢獻加總。

## 輸出格式（`web/data/detail.json`）

```
meta      latest, months（可選月份）, history（漲幅歷史月份）, groups（八大類：en/zh/color）,
          unpublished_months, max_level, baseline_years
headline  mom, yoy, core_mom, core_yoy, remainder_mom, remainder_yoy     （對齊 meta.months）
items[]   id, name, zh, level, parent, leaf, group, core, code, sa, unsampled, proxy
series    {id: {w, cm, cy  （對齊 meta.months：權重、月增率貢獻、年增率貢獻）
                gm, gy     （對齊 meta.history：細項自己的月增率、年增率，%）}}
```

- `proxy=True`：BLS 沒有這個細項的指數，數字是用上層細項估計的。
- `sa=False`：月增率用的是未季調指數。
- 缺值一律是 `null`（BLS 當月沒公布，或該年找不到權重）。

## 驗證（2026-08 資料）

| 比對 | 結果 |
|---|---|
| BLS 新聞稿 Table 2，56 個細項 | 7 月權重全部一致（最大差 0.001）；季調月增率 56/56；年增率 55/56（差的那一項是四捨五入邊界） |
| SF Fed 核心 CPI 細項月增率貢獻，2013 年起 6,522 筆 | 平均差 0.0005pp，95% 小於 0.0017pp；2026-08 前五名、後五名相同 |
| 未季調單月貢獻加總 vs 總體 | 平均差 0.012pp（差距大的月份是有細項當月沒公布） |
| 未分配 | 月增率平均 0.019pp；年增率平均 0.079pp（原因見 PITFALLS 第 11 點） |
