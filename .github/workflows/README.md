# GitHub Actions 說明

這個資料夾放的是 **GitHub Actions 的流程檔**（workflow）。
GitHub 會讀這裡的 `.yml` 檔，在它自己的雲端機器上照檔案寫的步驟自動執行，不需要你的電腦開著。
（這份 README 不會被執行，GitHub 只看 `.yml` / `.yaml`。）

| 檔案 | 名稱（Actions 頁面上看到的） | 一句話 |
|---|---|---|
| [`update.yml`](update.yml) | Update CPI data | BLS 公布新 CPI 後，抓資料、重算、檢查、存進 repo |
| [`pages.yml`](pages.yml) | Deploy site | 把 `web/` 資料夾發布成網站 |

```
            平日每天 3 次
                 │
        ┌────────▼─────────┐     沒有新資料
        │   update.yml     ├──────────────► 結束（大部分的日子）
        │  1. 有新月份嗎？   │
        │  2. 權重表缺了嗎？ │
        │  3. 重算貢獻度     │
        │  4. 自我檢查 ✗───────────────────► 失敗：停止，不發布，寄信通知你
        │  5. commit 資料    │
        └────────┬─────────┘
                 │ 網站資料有變
        ┌────────▼─────────┐
        │   pages.yml      │
        │  把 web/ 發布到    │
        │  GitHub Pages     │
        └──────────────────┘
```

---

## 先懂幾個 YAML / Actions 的基本概念

YAML 是一種用**縮排**表示層級的設定檔格式，跟 Python 一樣靠空白對齊，不能用 Tab。

| 寫法 | 意思 |
|---|---|
| `key: value` | 設定一個值 |
| 往右縮排的幾行 | 屬於上一行的子項目 |
| `- 某某` | 清單裡的一個項目（例如一個步驟） |
| `# 文字` | 註解，不會執行 |
| `run: \|` 後面接多行 | 一段多行的終端機指令，照順序執行 |
| `${{ ... }}` | GitHub 的變數，執行時才代入，例如 `${{ secrets.BLS_API_KEY }}` 會換成你設定的金鑰 |

Actions 的結構是三層：

- **workflow**（整個檔案）：一套流程，檔案最上面的 `on:` 決定**什麼時候**觸發
- **job**（`jobs:` 底下的每一塊）：在一台全新的雲端機器上跑的一組工作；每次都是乾淨的 Ubuntu，跑完就丟掉
- **step**（`steps:` 底下的每個 `-`）：job 裡一個一個照順序執行的步驟。
  `uses:` = 用別人寫好的現成步驟（例如 GitHub 官方的「下載程式碼」）；`run:` = 自己寫的指令

---

## `update.yml`：自動更新資料

### 什麼時候跑（`on:`）

```yaml
on:
  schedule:
    - cron: "45 13 * * 1-5"
    - cron: "45 15 * * 1-5"
    - cron: "45 19 * * 1-5"
  workflow_dispatch:
    inputs:
      force: ...
```

- **`schedule` / `cron`**：定時執行。`cron` 的五個欄位依序是「分 時 日 月 星期」，時間一律是 **UTC**：
  - `45 13 * * 1-5` = 週一到週五（`1-5`）的 13:45 UTC
  - 三行合起來是平日每天 13:45、15:45、19:45 UTC 各跑一次
  - BLS 在美東 08:30 公布，夏令時間是 12:30 UTC、冬令時間是 13:30 UTC，所以第一次一定在公布之後；
    後兩次是萬一第一次失敗（例如 BLS API 還沒更新）的重試
  - GitHub 的排程可能延遲幾分鐘到幾十分鐘，這是正常的
- **`workflow_dispatch`**：允許你在 GitHub 網頁上**手動按按鈕**執行
  （Actions → Update CPI data → Run workflow）。
  `force` 是按鈕旁的勾選框：勾了就算沒有新資料也強制重算並發布。

### 權限（`permissions:`）

```yaml
permissions:
  contents: write     # 可以 commit、push 到這個 repo（存新資料）
  pages: write        # 可以發布 GitHub Pages
  id-token: write     # 發布 Pages 時向 GitHub 證明身分用
```

每次執行時 GitHub 會發一把**臨時鑰匙**給這個流程，這裡寫的就是那把鑰匙能做什麼。
跑完鑰匙就作廢，跟你電腦上 `gh` 的登入無關。

### 不要同時跑兩次（`concurrency:`）

```yaml
concurrency:
  group: update-cpi
  cancel-in-progress: false
```

同一組（`update-cpi`）同時只會有一個在跑；如果前一次還沒結束，新的會**排隊等**，
不會兩個一起寫資料互相打架。

### 工作內容（`jobs:` → `update:`）

```yaml
  update:
    runs-on: ubuntu-latest        # 用一台 GitHub 提供的 Ubuntu 機器
    env:
      BLS_API_KEY: ${{ secrets.BLS_API_KEY }}
      BLS_USER_AGENT: ${{ secrets.BLS_USER_AGENT }}
```

`env:` 把你在 repo 設定的兩個 Secrets 變成環境變數，Python 程式會自己讀
（沒設也能跑，只是比較容易被 BLS 限流）。Secrets 的值在 log 裡會被自動遮成 `***`。

接下來的步驟依序是：

| # | 步驟 | 做什麼 | 失敗會怎樣 |
|---|---|---|---|
| 1 | `actions/checkout@v4` | 把這個 repo 的程式碼下載到機器上 | 流程停止 |
| 2 | `actions/setup-python@v5` | 安裝 Python 3.12（`cache: pip` = 記住套件，下次比較快） | 流程停止 |
| 3 | `pip install -r requirements.txt` | 安裝 numpy、openpyxl | 流程停止 |
| 4 | **Is a new release out?**（`id: gate`） | 跑 `schedule.py --download check`：更新 BLS 公布時間表，比對「BLS 已公布的最新月份」和「網站上的最新月份」，輸出 `run=true` 或 `run=false` | 流程停止 |
| 5 | **Official weight tables** | 只有在缺前一年 12 月權重表時才去 bls.gov 下載 | `continue-on-error: true`：**失敗也繼續**，改用 repo 裡已有的權重表 |
| 6 | **Compute contributions** | 先把舊資料備份一份，然後跑 `cpi_contrib.py` 重算（`--html ""` = 不產生本機用的 standalone 檔） | 流程停止 |
| 7 | **Sanity checks** | 跑 `checks.py` 的 30 項檢查（含和 Bloomberg 對帳、確認沒有弄丟舊月份） | **流程停止、不會發布**，GitHub 寄信通知 |
| 8 | **Commit** | 把變更存進 repo（見下） | 流程停止 |

步驟 5–7 都有 `if: steps.gate.outputs.run == 'true'`：意思是**只有第 4 步說有新資料時才做**。
沒有新資料的日子，第 5–7 步會顯示灰色的「skipped」，整個流程約 15 秒結束。

第 8 步（Commit）的邏輯：

- 什麼都沒變 → 不 commit，`changed=false`
- 只有公布時間表變了（例如 BLS 在頁面上加了明年的日期）→ commit「schedule: refresh BLS release calendar」，`changed=false`（網站不用重新發布）
- 網站資料 `web/data` 變了 → commit「data: CPI through 2026-09」，`changed=true`

commit 的作者會是 `github-actions[bot]`，所以你在 repo 歷史裡看到它就是這個流程做的。

### 接著發布（`jobs:` → `deploy:`）

```yaml
  deploy:
    needs: update                                   # 等 update 做完
    if: needs.update.outputs.changed == 'true' || inputs.force
    uses: ./.github/workflows/pages.yml             # 直接呼叫 pages.yml
```

只有在資料真的變了（或手動勾了 `force`）才發布網站。

為什麼要在這裡**直接呼叫** `pages.yml`，而不是等它自己被 push 觸發？
因為 GitHub 規定：流程用臨時鑰匙做的 push **不會觸發其他流程**（避免流程互相觸發、無限循環）。
所以 update 做完 commit 之後，要自己把 pages 叫起來。

---

## `pages.yml`：發布網站

### 什麼時候跑

```yaml
on:
  push:
    branches: [main]
    paths: ["web/**"]
  workflow_dispatch:
  workflow_call:
```

三種情況會觸發：

1. **`push`**：有人（你或我）推到 `main`，而且改到了 `web/` 底下的檔案（例如改網頁、改方法論）。
   只改 Python 程式或 README 不會觸發。
2. **`workflow_dispatch`**：在 Actions 頁面手動按 Run workflow。
3. **`workflow_call`**：被 `update.yml` 呼叫（上一節說的情況）。

### 權限與排隊

```yaml
permissions:
  contents: read      # 只需要讀程式碼
  pages: write
  id-token: write

concurrency:
  group: pages
  cancel-in-progress: false
```

同時只發布一個版本；正在發布時又來一個，新的會排隊，**不會把正在發布的那次取消**。
（之前設成 `true` 時，第一次部署就出現過「The operation was canceled」的紅字，所以改掉了。）

### 發布步驟

```yaml
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - uses: actions/checkout@v4
        with:
          ref: main
      - uses: actions/configure-pages@v5
      - uses: actions/upload-pages-artifact@v3
        with:
          path: web
      - id: deployment
        uses: actions/deploy-pages@v4
```

| 步驟 | 做什麼 |
|---|---|
| `environment: github-pages` | 標記這是「正式網站」的發布；repo 首頁右側的 *Deployments* 會顯示紀錄和網址 |
| `checkout`（`ref: main`） | 下載程式碼。指定 `main` 是為了拿到 update.yml **剛剛 commit** 的最新資料 |
| `configure-pages` | GitHub 官方步驟，準備 Pages 設定 |
| `upload-pages-artifact`（`path: web`） | 把 `web/` 整個資料夾打包上傳。**網站上看得到的就只有 `web/` 裡的東西**，Python 程式、原始權重檔都不會出現在網站上 |
| `deploy-pages` | 把打包好的檔案發布到 <https://jasonshin1996.github.io/us-cpi-driver/>，通常 1–2 分鐘 |

log 裡如果看到 `DeprecationWarning: The punycode module is deprecated`，那是 GitHub 官方套件自己的警告，可以忽略。

---

## 常見操作

| 想做的事 | 怎麼做 |
|---|---|
| 看最近有沒有跑、成功沒 | repo 上方 **Actions** 分頁；綠勾 = 成功、紅叉 = 失敗、灰色 = 跳過 |
| 立刻更新一次 | Actions → **Update CPI data** → Run workflow（要強制重算就勾 `force`） |
| 只重新發布網站 | Actions → **Deploy site** → Run workflow |
| 設定 BLS 金鑰 | Settings → Secrets and variables → Actions → New repository secret，名稱 `BLS_API_KEY` |
| 暫停自動更新 | Actions → Update CPI data → 右上角 ⋯ → **Disable workflow** |
| 改排程時間 | 改 `update.yml` 的 `cron` 那幾行（記得是 UTC） |

失敗時，GitHub 會寄信到你帳號的信箱。點進那次執行，展開紅叉的步驟就能看到錯誤訊息；
常見原因和處理方式寫在專案根目錄 [README.md](../../README.md) 的〈維護須知〉。

> 附註：如果 repo **連續 60 天沒有任何 commit**，GitHub 會自動停用排程流程並寄信通知。
> 這個專案每個月都會有資料 commit，正常情況下不會碰到；萬一被停用，到 Actions 頁面按 *Enable workflow* 即可。
