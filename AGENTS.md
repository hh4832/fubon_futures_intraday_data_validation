# 工作規範
本 repo 僅驗證市場行情資料，不研究策略，不加入下單功能。
GitHub 為 source of truth；Python 3.11、Google Colab 為主要環境。
官方文件與 observed behavior 分開；未觀察欄位不得猜測。
不得製造連續期貨、換月調整或補分鐘；observed gap 不等於資料缺漏。
交易日歸屬與 bucket alignment 必須實證；不得僅因 API 有資料宣稱可歷史回測。
所有結論可追溯 raw response、設定與 Git commit；Drive 每次建立獨立 Asia/Taipei timestamp archive。
Secrets、憑證、binary wheel 與研究輸出不得 commit。
登入與 SDK 失敗立即停止；個別行情端點失敗需記錄並繼續其他驗證。
驗證使用向量化；測試不依賴 SDK 或帳密。修改後執行 pytest 與 Notebook 靜態檢查。

## Historical phase
連續合約必須先驗證 rollover；1! 不可直接視為可回測的 continuous return series。
嚴格依 phase 1→7 gates；FAIL/UNRESOLVED 阻擋後續，不能把未執行項目當 PASS。
不得 silent back-adjustment、forward fill、刪除 mismatch 或改寫 canonical intraday output。
historical vs intraday 是 provider internal consistency；external validation 需獨立標示。
單日 PASS 不等於多年 PASS；固定年度樣本不得因結果不佳更換。
Documented fact / Observed fact / Inference / Unresolved 必須區分。
夜盤交易日映射須使用具來源 session windows，不能將週末/假日視為普通 +1 天。
calendar 超出已審核範圍必須停止；calendar/reference count 不能充當 confirmed missing-data 證據。

## Independent continuous rollover phase
Rollover-only entrypoint 只查固定 2026-09-10～09-22 regular 1m 的 1!/202609/202610。
三組 from/to 必須完全一致，包含舊契約到期後期間，不因結果改 window。
此獨立入口基於已完成 historical baseline，不呼叫原 historical phase 1→7 runner。
Exact OHLC/volume comparison 不允許 tolerance；adjustment-model tolerance 僅用於診斷，不改資料。
不得用 missing expired-contract volume=0 判定 crossover；空缺保持 NaN。
重複 timestamp 不得 silent drop；comparison projection 可排除 ambiguous reference keys，但 raw/data/anomalies 必須完整保留。
Observed transition、expiry/volume consistency 與 provider rule proof 必須分開；單一窗口不授權多年 returns。

## Independent robustness phase
最新 robustness 入口只查六個事先固定 rollover windows × regular/afterhours × 1!/old/new，共 36 個 logical historical.candles requests；1m only。
樣本/expiry/window/calendar 事前固定、有官方來源，不因結果改換；保留 calendar_date 與 mapped trade_date。
不呼叫既有 phase runners；沿用 auth、bounded retry、raw archive 與 exact matching helpers。
分開統計 active underlying 與 inactive 月份缺分鐘；schedule absence 不是 confirmed loss，active asymmetric gap 阻擋 promotion。
未解釋的 partial-session mixing、缺失 identity、OHLC transformation 或不可重建 boundary 阻擋。
所有 sampled construction operationally traceable 可保留／進另行 final integrity validation；不要求識別私有演算法名稱。
此六窗口結論不可外推全歷史，不執行 final integrity、external price validation、策略或 returns。

## Final cross-year historical integrity phase
獨立 final runner 保留 frozen 2017–2026 三窗口planned矩陣（190 dates、全plan108 requests），只執行freeze-time as-of eligible窗口；2026-10 as-of為185eligible dates、106 requests。
官方 calendar/expiry/session exceptions 在首次API前固定並hash，每request前核對；未完成future窗口保留planned dates，NOT_YET_ELIGIBLE / FUTURE_WINDOW，不納入formal requests或data-quality判定；included incomplete窗口仍阻擋，不改樣本。
同源yearly rollover確認不是重新discovery；internal aggregation不查provider higher timeframes。
Independent TAIFEX daily spot checks只比definition-compatible欄位；NOT COMPARABLE不是FAIL，但無compatible external證據不能promote。
任何year blocker、repeatability mismatch、availability未界定或external明確矛盾都阻擋canonical source推薦；最終data PASS不等於策略有效。
