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
