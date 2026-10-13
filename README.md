# 富邦期貨日內行情資料驗證

[在 Google Colab 開啟](https://colab.research.google.com/github/hh4832/fubon_futures_intraday_data_validation/blob/main/notebooks/validate_fubon_futures_intraday.ipynb)

This project validates Fubon futures intraday market-data behavior.
It does not assume that the intraday candles endpoint provides historical multi-date data.

本專案以少量 API 請求，驗證 schema、實際 TXF 合約、時間戳、日夜盤、OHLC、平均價與 K 線聚合一致性。
不設計策略、不計算績效、不下單、不製造連續合約、不進行大量歷史下載。

開發驗證狀態：**STATIC TESTED / COLAB/API NOT YET TESTED**。
正式 Colab 執行結果另存獨立 archive；離線 synthetic 測試不代表真實 API 已通過。

## Colab Run All

1. 點上方 Colab 連結。
2. 將富邦憑證放入私人 Google Drive；使用 API Key 登入所需的有效憑證。
3. 在 Colab 左側 Secrets 設定以下名稱，允許此 Notebook 使用。
4. Run All，授權 mount Drive。SDK 安裝、登入失敗會明確停止。
5. 至最後一格顯示的 Drive 路徑檢查結果；即使單一行情請求失敗也會繼續其他組合。

| Secret | 內容 |
|---|---|
| `FUBON_PERSONAL_ID` | 身分證字號 |
| `FUBON_API_KEY` | 富邦 API Key |
| `FUBON_CERT_PASSWORD` | 憑證密碼 |
| `FUBON_CERT_PATH` | 私人 Drive 憑證的完整路徑，例如 `/content/drive/MyDrive/private_certificate/certificate.pfx` |

public repo 不需 GITHUB_TOKEN；未來改 private 時可額外設定 `GITHUB_TOKEN`，clone token 不會寫入 remote 或顯示。
不儲存登入 raw response；帳號僅遮罩顯示。任何初始化失敗不列印 SDK 原始錯誤。

## SDK / Python

主要測試 Python 3.11；Colab kernel 使用 Python 3.8–3.13 Linux x86_64 時可安裝。
`requirements.txt` 僅 PyPI 依賴；Fubon Neo 由 `scripts/install_fubon_sdk_colab.py` 安裝官方 wheel。
固定 SDK 2.4.0（2026-10-12 官方頁面）；相同版本已安裝時不重裝。禁止 unofficial mirror。
官方未提供可信 checksum，因此 `sdk_official_sha256` 為 null；實際下載 SHA256 會記錄，但不宣稱官方 hash 驗證。
Colab Python 版本若超出官方相容範圍會停止，不自行替換 kernel。

## API 與版本資訊

SDK client：`sdk.marketdata.rest_client.futopt`。
登入使用 `sdk.apikey_login(personal_id, api_key, cert_path, cert_password)`，接著 `sdk.init_realtime()`。
行情 endpoint：`intraday/products`、`intraday/tickers`、`intraday/candles/{symbol}`。
先 products 查 TXF，再 tickers 取得月份契約；選擇最近尚未到期的 API settlementDate/endDate。
「最近月份」並不等於已證明成交量最大的主力契約，選取理由會記錄。無可靠日期時可在設定指定 discovery 出現過的 symbol，絕不猜月份碼。
products/tickers 的 session 為 `REGULAR` / `AFTERHOURS`；candles 日盤省略 session，夜盤使用 `afterhours`。
每個契約測試日夜盤 × 1/5/15/60 分，共 8 組；順序呼叫，429 最多 3 次 bounded retry。

重要文件更新：2.4.0 官方新增**獨立** `historical/candles/{product}`，列有歷史日期等參數。
本專案只檢查該 callable 是否存在與文件資訊，不呼叫該 endpoint。
不得將 intraday 不支援歷史日期誤解為整個富邦 API 不支援歷史資料。文件不等於實際驗證。
詳見 [官方來源](docs/official_sources.md)。

## 結構

- `config/validation_config.yaml`：固定小樣本範圍、SDK、Drive、reference 時段。
- `scripts/install_fubon_sdk_colab.py`：官方安裝與 import 檢查。
- `src/fubon_auth.py`、`fubon_client.py`：安全登入、read-only 請求 allowlist、重試與 raw 保存。
- `src/discover_contracts.py`、`fetch_intraday.py`：合約探索與 8 組日內資料。
- `src/normalize.py`：保留原始欄位/source_row、Asia/Taipei 時間戳、numeric coercion。
- `src/validate_*.py`：schema / OHLC / time / sessions / aggregation。
- `src/pipeline.py`、`diagnostics.py`、`archive.py`：staged orchestration、summary、provenance。
- `notebooks/validate_fubon_futures_intraday.ipynb`：25 個順序工作步驟。
- `tests/`：不依賴 SDK/帳密的 synthetic unit/integration tests。
- `outputs/.gitkeep`：不存正式結果；raw/parquet、憑證、wheel 排除 Git。

## 驗證判讀

- schema：缺欄位、每根 null、metadata、dtype、empty response。
- OHLC：正價格、高低邏輯、非負成交量、平均價正值。
- timestamp：保留原順序以辨識升降序、重複、非單調；naive timestamp 轉成 NaT 並回報，絕不推定時區。
- observed gaps：只記錄，不能自行宣稱 confirmed missing data；不補分鐘。
- session：比較 broad reference 日盤 08:45–13:45、夜盤 15:00–05:00，例外僅 caveat；交易日歸屬保持實際觀察。
- aggregation：枚舉少量時間 bucket convention，完整穩定 bucket 比較 OHLCV；排除缺分鐘 bucket、最新一根與跨交易日資料。
- alignment：至少 2 根完整且一致 bucket 才標實證一致；無法確定標 `UNRESOLVED_ALIGNMENT`，不能直接宣稱 API 錯誤。
- average：區間與 cumulative implied diagnostics，沒有成交明細無法證明語意，預設 `unclear`。
- history：signature/docstring + 文件比對 + 一次 date probe；`**kwargs` 接受參數不等於歷史查詢成功。

各項結論為 PASS / PASS WITH CAVEATS / FAIL / UNRESOLVED。Aggregation unresolved alignment 屬 UNRESOLVED。
反對者觀點：單日 snapshots 不能證明多年歷史完整性、夜盤交易日歸屬、換月或 VWAP 定義。

## Drive 輸出

固定 root：

`/content/drive/MyDrive/00Quant_Research/fubon_futures_intraday_data_validation/`

[對應資料夾](https://drive.google.com/drive/folders/1FsM0ti9rsoCFGbLQLM26N2sh-nxyWwK5)

每次建立 `YYYYMMDD_HHMMSS_<git_commit>/`，時區 Asia/Taipei；碰撞時 fail，不覆蓋。
正式執行要求 clean Git，保存完整 commit、branch、環境/套件、SDK URL/hash、設定快照、請求次數及 error/rate limit。
檔案從 response 取得起直接寫到該 Drive archive，最後驗證檔案存在；中斷保留 IN_PROGRESS 狀態。

| 輸出 | 內容 |
|---|---|
| `raw/*_attemptN.json` | 每次請求 metadata、完整 JSON response 或安全 error；包含 discovery、429 retries、歷史 probe |
| `data/*.parquet` | 正規化 K 線、original values、來源順序、異常 OHLC |
| `diagnostics/schema_validation.csv` | schema、metadata、型態、null、empty response |
| `diagnostics/ohlc_validation.csv` | OHLC 異常計數 |
| `diagnostics/invalid_ohlc_rows.parquet` | 全部異常 K 線；正常時仍有 empty schema |
| `diagnostics/timestamp_validation.csv` | timezone、重複、原始排序、gap |
| `diagnostics/session_validation.csv` | 日夜盤時間範圍與交易日 |
| `diagnostics/aggregation_validation.csv` | 候選 alignment 與一致性摘要 |
| `diagnostics/aggregation_detail_*.csv` | 各根 open/high/low/close/volume 差異 |
| `diagnostics/documentation_vs_observed.csv` | 文件欄位與實際 schema 比對 |
| `diagnostics/historical_capability.csv` | intraday signature/probe 及獨立 history endpoint 狀態 |
| `diagnostics/average_validation.csv` | average diagnostics，語意保持 unclear |
| `diagnostics/api_requests.csv`、`performance.csv` | retries / errors / row_count / elapsed_seconds |
| `validation_summary.md`、`.txt` | 分項判定與限制 |
| `run_info.txt`、`config_snapshot.json` | 執行環境、Git provenance、設定 |

## 離線開發

使用既有 Python 3.11 虛擬環境，安裝 `requirements.txt` 後：

```bash
python -m pytest -q
python scripts/validate_repository.py
```

測試不 import 富邦 SDK；不需要 credential。真實 SDK 安裝、登入、合約及資料仍需使用者在 Colab 驗證。

## Historical Candles Validation

已有 canonical initial intraday validation：SDK 2.4.0、API Key login 與 TXF 日夜盤行情可用；
canonical run 為 `20261013_051100_cfe1f938b414`。本分支不改寫 intraday baseline 或其 outputs。
注意 canonical run 的 `account_types=[stock]`、`futopt_account=false`，但 futures market-data call 成功；
行情存取成功不代表登入 response 出現 futures trading account。

Historical validation **≠ strategy validation**。新 phase 驗證跨日/年 coverage、cross-endpoint consistency、
實際 contractMonth、單一 rollover window、夜盤歸屬與跨年度固定樣本；不建立正式回測資料庫。

### 使用方式

- 分支：`feature/historical-candles-validation`，Notebook：`notebooks/validate_fubon_futures_historical.ipynb`。
- 同樣使用四個 Colab Secrets 與私人 Drive 憑證，Notebook 以獨立 clone directory 保留原 intraday checkout。
- **預設 `MAX_PHASE=1`**，先查最近五個已完整結束的 regular 1! / 1m sessions，重複查一次檢查可重現性。
- 確認最小 sample 結果後可在 Notebook 設定 `MAX_PHASE=2..7`；每次 run 仍從 Phase 1 重新依序 gating，不跳步。
- FAIL 或 UNRESOLVED 阻擋後續；PASS WITH CAVEATS 可繼續且保留 caveat。
- 每次建立新的時間戳 archive；正式 run 必須 clean Git，尚未提交的開發版本不執行 formal run。

| Phase | 驗證 |
|---|---|
| 0 | Environment / Git / authentication / canonical documentation |
| 1 | Historical 1! regular 1m，最近 5 完整 sessions，重複查詢 |
| 2 | 同一 discovered actual contract、完整交易日的 historical vs intraday |
| 3 | 小段具體 `202610` contractMonth 與 1! 比對 |
| 4 | `2026-09-10..22` 單一 rollover；1!、202609、202610 |
| 5 | 最近 5 完整夜盤，並在相同 rollover window 比較 session transition |
| 6 | regular / afterhours 1m vs 5/15/60m，逐交易日分桶 |
| 7 | 固定 2026/2025/2024/2022/2020/2018/2017 小樣本 |

### Historical API 依據與限制

先讀 canonical run 的 `diagnostics/official_historical_documentation.txt`；預期 SHA256 記在 historical config。
缺檔或 hash 改變時停止，不重新發明 API 參數。文件來自使用者已保存的 official archive。
本次開發已用官方 SDK 2.4.0 實際 introspect dependency implementation，method 為 `candles(self, **params)`。
formal run 仍對實際登入後 historical client 重新 introspect，寫入 `historical_callable.txt`。
安全 object repr 不輸出 client 內部 auth configuration。

官方文件明載 `product / contractMonth / from / to / timeframe / session / fields / sort`。
Historical top metadata 與 intraday 不同，不會把 intraday schema 套用到 historical。
每根 K 線 `contractMonth` 保存 actual month；query `1!` 與 actual month 分開，不自行猜 actual symbol。
文件說 historical average 是每根 K 線 volume-weighted price，程式將此列 **Documented fact**；
實際 identity/reset semantics 仍保留 **Unresolved**，不假定等於 intraday average。

Phase 2 用 intraday discovery 的 settlementDate 對應實際月份與 symbol，沒有可靠 identity 就停止後續。
若 intraday 已切至新交易日，而 historical sample 僅含之前完整日期，overlap 標 UNRESOLVED，
不把無重疊、未知參數被忽略或相同 provider 自洽當作外部驗證。

Rollover 每根用 raw OHLCV＋observed month metadata 比對，雙合約同價視為 ambiguity；
`100,101,103,104` 支持 sampled raw concatenation，`102,103,103,104` 僅標 transformed / requires further inference。
不據單一窗口直接證明 expiry-trigger vs volume-trigger 的通用規則；no adjustment 只針對取樣 segment。
`2!` / `3!` 僅支援介面與文件參數，本輪不取樣，結論保持未驗證。

### 交易日與 fixed samples

`config/historical_session_calendar.csv` 是具來源的 scoped scheduled reference，涵蓋 2026-09-10 至 2026-10-30。
日期依 TAIFEX 2026 calendar 與 9/25、9/28、10/9、10/26 休市；night windows 依前一 scheduled regular date。
未額外涵蓋臨時停市；calendar 超出範圍會明確停止，必須先更新來源，不可自動假設 weekday 都交易。
完整 session 除結束時間外加 60 分鐘 conservative publication delay，這是 **Inference**，不是官方更新時刻。
到期短 session 及 window 邊界異常保持 caveat，reference missing_slot_count 不等於確認資料缺漏。

cross-year dates 在 `config/historical_validation.yaml` 固定：每年 5 個日期，2017 從 6/1 起。
早期年月樣本未有完整、獨立交易日表，calendar/source uncertainty 在 coverage 保留 caveat；
不能遇缺資料就換日，也不能據固定六月樣本宣稱全季節穩定。未來擴展 calendar 需另行驗證。

### 新增輸出

除原有 raw/data/run_info 外，保存：

- `historical_callable.txt`、canonical documentation 與 calendar snapshot。
- `historical_schema_validation.csv`、`historical_ohlc_validation.csv`。
- `historical_timestamp_validation.csv`、`historical_session_validation.csv`、`historical_daily_coverage.csv`。
- `historical_api_requests.csv`、`historical_performance.csv`、`historical_stages.csv`。
- `historical_repeatability.csv`、`historical_vs_intraday_overlap.csv`、`historical_vs_intraday_summary.csv`。
- `concrete_vs_continuous.csv`、`rollover_mapping.csv`、`rollover_semantics.csv`。
- `historical_afterhours_validation.csv`、night rollover mapping、session comparison。
- `historical_aggregation_validation.csv`、`cross_year_coverage.csv`。
- `historical_average_validation.csv`；average unclear 不會偽裝成 VWAP empirical proof。
- `historical_external_validation.csv`：預設 `NOT PERFORMED`；外部 minute reference 可指定 CSV＋可追溯 source，
  欄位為 timezone-aware timestamp、actual_contract_month、OHLC、volume、average，程式保留 exact comparison interface。
- `validation_summary.md/.txt`：A–Q 問題、四類 evidence、PASS/PASS WITH CAVEATS/FAIL/UNRESOLVED 與研究結論。

新增模組：`historical_client.py`、`historical_normalization.py`、`historical_validation.py`、
`historical_overlap.py`、`rollover_validation.py`、`historical_runner.py`。
重用現有登入、官方 installer、retry/raw writer、archive、OHLC/time/aggregation primitives。
開發完成仍不代表 live historical API 成功；沒有憑證時僅執行 synthetic tests。

## Continuous Contract Rollover Validation

獨立分支 `feature/continuous-contract-rollover-validation`，基於 historical baseline `a8169cc`。
新 Notebook：`notebooks/validate_fubon_futures_rollover.ipynb`；Run All 預設只跑單一 regular 1m 換月窗口。
沿用原本 Secrets、官方 installer、read-only client、raw writer、normalization 與獨立 Drive archive。

### 固定範圍

`TXF`、`regular`、`1m`、`asc`、`open,high,low,close,volume,average`。
三組 `1!`、`202609`、`202610` **全部使用 2026-09-10～2026-09-22**，沒有縮短舊契約窗口。
Expiry reference 為 `2026-09-16`；只描述實際切換與 before/on/after 的一致性，不預設 expiry-based rule。
本入口不呼叫 historical phases、不抓 cross-year/afterhours/higher timeframe、不做 production download。
設定位於 `config/continuous_rollover_validation.yaml`；固定核心範圍若變動會停止。

### 比對與判讀

- 保留每根 `actual_contract_month`、query `response_contractMonth`，按實際月份對照 raw contract。
- OHLC 與 volume **exact** 比較，不用 tolerance 掩蓋 mismatch；average 單獨列出。
- 重複 source bars 全保留；comparison-only projection 排除 ambiguous reference keys，明確寫入 anomalies，避免 many-to-many join。
- 比較切換前一天、切換當天與後一天，每段至少 2 根完整 bars；缺 anchor date 不換日。
- Constant offset / ratio 使用預先固定的數值精度診斷，但不改變 exact matching 或原始價格。
- 只在各段與所有 observed bars 都吻合後，支持 sampled raw/unadjusted concatenation；不得推廣成多年 rule。
- Roll gap 是跨契約 transition gap，可能包含隔夜市場變動，不能當市場真實報酬。
- Volume leadership 僅用 observed minute-volume sums；缺少到期後舊契約資料保持 NaN，不補 0。
- 同源比較不是 external validation；單一 expiry/volume timing 一致不能證明 provider 的唯一規則。

### 輸出

`raw/rollover_1bang_attemptN.json`、`rollover_202609_attemptN.json`、`rollover_202610_attemptN.json`。
`data/rollover_1bang.parquet`、`rollover_202609.parquet`、`rollover_202610.parquet`。
以下 CSV 保存於新 archive 的 `diagnostics/`：

| 檔案 | 用途 |
|---|---|
| `rollover_contract_coverage.csv` | 每日 actual months、首末時間、partial-day switch |
| `rollover_mapping.csv` | 所有 continuous bars 與 old/new raw references、exact flags |
| `rollover_transition_summary.csv` | 切換 timestamp/day、expiry consistency、roll gap、volume consistency |
| `rollover_price_comparison.csv` | 三組 OHLC、exact flags、close differences |
| `rollover_volume_comparison.csv` | 每日 minute-volume totals、leadership crossover、simple rule counterexamples |
| `rollover_adjustment_test.csv` | 前一天／當天／後一天 raw/additive/ratio evidence |
| `rollover_daily_summary.csv` | 每天是哪個月份、三組 row counts、raw match |
| `rollover_anomalies.csv` | third month、partial-day switch、duplicates、gaps、missing references、mismatches |
| `rollover_field_comparison.csv` | OHLC/volume/average exact match rates 與 unavailable counts |
| `rollover_input_validation.csv`、`rollover_api_requests.csv`、`rollover_performance.csv` | schema/numeric quality、requests/retries、執行時間 |

`run_info.txt` 記錄 `validation_phase=rollover`、`validation_stage=continuous_contract_rollover`、
Git commit、window、old/new/continuous contract、expiry、requests/errors；不寫入 secrets。
`validation_summary.md/.txt` 回答 A–P 問題、列出反對者觀點與 evidence labels。

開發測試不使用 credentials。Live rollover 尚待 Colab 執行；mechanism 不清楚時結論為「修改後再測」。

## Rollover Robustness + Night Session Validation

Colab 入口：[validate_fubon_futures_rollover_robustness.ipynb](https://colab.research.google.com/github/hh4832/fubon_futures_intraday_data_validation/blob/feature/rollover-robustness-validation/notebooks/validate_fubon_futures_rollover_robustness.ipynb)。使用 clean `feature/rollover-robustness-validation` branch；Python 3.11、SDK/auth/Drive 設定沿用既有流程。

獨立 `src.rollover_robustness.run_rollover_robustness` 只執行 36 組 sequential historical 1m requests（最多各 3 attempts），沒有 intraday、higher timeframe、phase 1→7 或 final cross-year 執行。六個不可依結果變更的樣本、官方 expiry metadata 與四個交易日前後窗口在 `config/rollover_robustness_validation.yaml`；具來源 session windows 在 `config/rollover_robustness_session_calendar.csv`。來源與限制見 [calendar metadata](docs/rollover_robustness_metadata.md)。

輸出至既有 Drive root 下新建的 Asia/Taipei `YYYYMMDD_HHMMSS_commit`；包含每組 raw attempts、完整 normalized parquet、13 組要求的 CSV，另存夜盤逐筆 mapping/coverage/identity transition、input validation、calendar snapshot、performance 與 A–R/15 問 summaries。`run_info` 分開 logical requests 與實際 retry attempts；認證/時間/schema fatal error 停止並留下安全診斷，未完成樣本不得通過。

OHLC/volume exact match 無 tolerance；average mismatch 獨立呈現。Adjustment tolerances 只用於 offset/ratio 診斷，不改資料。夜盤 trade_date 依官方「次一一般交易時段」規則與明列 windows 映射，保留 session_start_calendar_date，避免把 9/16 15:00 的夜盤誤稱 regular trade_date 9/16。Partial/intraday/cross-midnight switch 都會顯示並阻擋未解釋混約。

缺分鐘表按 sample/session/trade_date/query/underlying role 分開；inactive gaps 不影響 `1!` 評級。具 active reference bar 而 `1!` 缺 bar（或相反）為 unresolved blocker；雙方相同的 scheduled-slot absence 是未確認 no-trade/provider omission 的 caveat，不自稱已確認資料遺失。每個 requested session 完全空白則阻擋。到期月 regular reference 13:30 結束，其他月份 13:45；特殊 13:45 bar 保留，不固定要求 300 bars。

六個樣本 raw construction 都可追溯且無 blockers，可判 `PASS WITH CAVEATS` / `保留`，私有 trigger 名稱未知不構成 blocker。Regular/night 邊界不同要明確呈現，不能預設一致；volume 只反證 simple observed minute-volume-max，expiry alignment 不是 universal rule proof。這不驗證 2017–2026 全歷史；external validation 明列 `NOT PERFORMED`。下一階段需另行執行，roll_gap_return 僅為 transition gap，不是市場報酬。

### Minimal correction: failed robustness run replay

`20261014_060519_d3d96794bd6b` 的三份 regular parquet 可離線重現：到期日 reference end 原為固定 `UTC+08:00`，calendar start 則為 `Asia/Taipei`。`pd.date_range` 要求兩端 timezone identity 一致；修正只將既定 13:30 endpoint `tz_convert(start.tz)`，不改時刻、日期、samples、API semantics 或 validation thresholds。Single-rollover runner 不呼叫新 `missing_minutes`；原短 synthetic sessions 的 close 早於 13:30，因此未覆蓋該分支。月份 coverage legacy 欄名是 cfg-driven 值，robustness 已 rename 成 generic names，並非本次 root cause。

```python
from scripts.replay_rollover_robustness import replay_archive
output, replay_info = replay_archive(
    "/content/drive/MyDrive/00Quant_Research/fubon_futures_intraday_data_validation/20261014_060519_d3d96794bd6b",
    "/content/drive/MyDrive/00Quant_Research/fubon_futures_intraday_data_validation/NEW_TIMESTAMP_COMMIT_replay",
)
```

也可用 `python -m scripts.replay_rollover_robustness --archive PATH --output NEW_PATH`。輸出必須為新的、source archive 外的目錄。Config snapshot、sample metadata、calendar snapshot 必須一致；只讀原 parquet、不重做 normalization、不呼叫 SDK/API。`replay_status=PASS` 表示 analysis 成功執行，`analysis_classification` 仍保留原本 PASS/CAVEATS/FAIL/UNRESOLVED；input hashes 與 code Git provenance 存於 `replay_info.json`，不冒稱 formal success。

Notebook 已加入 pytest → archive replay → `run_first_regular_smoke` 的 gates。Smoke 只查既定 202606→202607 regular 三組原始查詢，保留 raw/data/diagnostics，寫入另一個新的 archive 並標示 `rollover_robustness_smoke`。只有全部 gates 通過，才另建 36-request formal archive；STOPPED archive 不覆寫或重用。

Fatal handling 現在保留 `diagnostics/fatal_traceback.txt`、`fatal_error_type` 與遮蔽敏感值後的 `fatal_error_message`，並 `raise ... from exc` 保留 cause。Traceback 不 dump locals，不將 SDK credentials/account values 寫入輸出。
