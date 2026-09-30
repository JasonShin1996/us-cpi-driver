# US CPI Driver：給接手的人與 AI agent

美國 CPI 貢獻度拆解：從 BLS 抓 CPI-U 指數與官方權重（relative importance），算出各細項對年增率、
月增率的貢獻，發布成靜態網站（GitHub Pages）。完整說明見 [README.md](README.md)。

## 動手前必讀

- [detail/PITFALLS.md](detail/PITFALLS.md)：BLS 資料的陷阱與處理方式（縮排錯誤、細項改名、
  2025-10 缺資料、12 月兩套權重、bls.gov 擋抓取……）。**改任何計算或解析程式之前先讀。**
- [web/methodology.html](web/methodology.html)：所有公式（中英雙語）。
- [.github/workflows/README.md](.github/workflows/README.md)：自動更新與發布流程。

## 結構

| 路徑 | 用途 |
|---|---|
| `cpi_contrib.py` | 主頁資料：四大類／七細項的貢獻度 → `web/data/cpi_data.*` |
| `ri_official.py` | 下載、解析 BLS 官方 12 月權重表 → `ri_official*.csv` |
| `schedule.py` | BLS 公布時間表與「是否有新資料」的判斷 |
| `checks.py` | 發布前自我檢查（含 Bloomberg 基準），不過就不發布 |
| `detail/` | 細項頁資料（拆到 level 5）：`cpi_detail.py`、說明與陷阱文件 |
| `web/` | 網站（`index.html` 主頁、`methodology.html`、`data/`），GitHub Pages 只發布這個資料夾 |

## 常用指令

```bash
pip install -r requirements.txt
python cpi_contrib.py && python detail/cpi_detail.py && python checks.py
python -m http.server -d web 8000      # 本機預覽
```

## 慣例

- **新功能開 feature branch**，不要直接改 `main`；`main` 的 push 會自動發布網站。
- **數字要驗證**：改了計算就跑 `checks.py`；新增資料來源時，找一個外部基準（BLS 新聞稿、
  Bloomberg、SF Fed）實際比對，並把結果寫進 README 或 methodology。
- **中英雙語**：網頁文字與 methodology 的中文、英文版本要同步修改。
- **發現新的資料陷阱就寫進 `detail/PITFALLS.md`**（症狀 → 原因 → 處理 → 怎麼發現）。
- **對照 BLS 代碼前先查 `cu.item`**，不要憑印象填（曾把 `SEEB04` 誤填成 `SEEB03`）。
- **不要 commit**：`cache/`、`cpi_dashboard_standalone.html`（已在 `.gitignore`）。
- **對 bls.gov 的請求**要帶含 email 的 User-Agent（環境變數 `BLS_USER_AGENT`）。
